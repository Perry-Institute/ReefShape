"""
Runs a batch of jobs as headless Metashape processes.

One QProcess per job. That is the whole design, and it buys three things that
are awkward or impossible in-process: a Metashape crash fails one job instead
of the app, cancelling is a process kill rather than a cooperative check, and
running several jobs at once is just several processes.

Jobs are written to temporary JSON files and picked up by `worker/run_job.py`.
Their stdout comes back as a mix of Metashape's own logging and our protocol
lines (see protocol.py); the former goes to a per-job log file, the latter
drives the GUI.

Resuming is inherited rather than implemented: the workflow guards every stage
on whether its product already exists and saves after each one, so a killed or
crashed job re-run continues from where it stopped. Retry is therefore just
"queue it again".
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Dict, List, Optional

from .qt import QtCore, Signal
from . import models, protocol
from .models import Job
from .metashape_locate import Install, find_install, MetashapeNotFound


# Above this, jobs contend for the GPU and memory badly enough that total
# throughput drops. Not enforced -- a machine with several real GPUs can go
# higher -- but the GUI warns past it.
RECOMMENDED_MAX_PARALLEL = 2

# How long to wait for a killed process to exit before escalating.
TERMINATE_GRACE_MS = 5000


class JobProcess(QtCore.QObject):
    """One running job: its process, its log file, and its parsed progress."""

    def __init__(self, job: Job, install: Install, parent=None):
        super().__init__(parent)
        self.job = job
        self.install = install
        self.process = QtCore.QProcess(self)
        self.cancelled = False
        self._buffer = ""
        self._log_handle = None
        self._job_file = ""
        self._result_file = ""

    # -- lifecycle --

    def start(self) -> None:
        job_dict = self.job.to_dict()

        handle, self._job_file = tempfile.mkstemp(
            prefix="reefshape_job_", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(job_dict, fh, indent=2, default=str)

        handle, self._result_file = tempfile.mkstemp(
            prefix="reefshape_result_", suffix=".json")
        os.close(handle)

        self.job.log_path = self._open_log()

        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)

        self.job.status = models.RUNNING
        self.job.started_at = time.time()
        self.job.current_step = "Starting Metashape"
        self.job.progress = 0.0

        program, arguments = self.command()
        self.process.start(program, arguments)

    def command(self):
        """The executable and arguments used to run this job.

        Metashape's `-r` takes a script path and passes everything after it
        through to that script as argv. Kept as its own method so the
        invocation is stated once, and so tests can substitute a stand-in
        worker that runs in seconds instead of hours.
        """
        return str(self.install.executable), [
            "-r", _worker_script(), self._job_file, self._result_file]

    def cancel(self) -> None:
        """Stop this job. The partial result stays on disk and can be resumed.

        terminate() first so Metashape gets a chance to close its project
        cleanly; kill() only if it ignores that. A hard kill mid-write is
        survivable -- the project is saved after each stage, so the worst case
        is losing the stage in flight -- but it is not the first choice.
        """
        self.cancelled = True
        if self.process.state() == QtCore.QProcess.NotRunning:
            return
        self.process.terminate()
        if not self.process.waitForFinished(TERMINATE_GRACE_MS):
            self.process.kill()
            self.process.waitForFinished(TERMINATE_GRACE_MS)

    def cleanup(self) -> dict:
        """Close the log, read the result file, and remove the temp files."""
        if self._log_handle:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

        result = {}
        try:
            with open(self._result_file, "r", encoding="utf-8") as fh:
                result = json.load(fh)
        except (OSError, ValueError):
            pass

        for path in (self._job_file, self._result_file):
            try:
                os.unlink(path)
            except OSError:
                pass
        return result

    # -- output handling --

    def _open_log(self) -> str:
        """Per-job log beside the project, named for the project and chunk.

        Beside the project rather than in a temp directory because this is the
        file a user is asked for when a plot goes wrong, months later, and the
        project is the thing they still have.
        """
        folder = self.job.project_folder() or tempfile.gettempdir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            folder = tempfile.gettempdir()

        name = "{}_{}_batch.log".format(
            self.job.project_name() or "job", self.job.chunk_name or "chunk")
        path = os.path.join(folder, name)
        try:
            self._log_handle = open(path, "a", encoding="utf-8", errors="replace")
            self._log_handle.write(
                "\n=== ReefShape Batch run {} ===\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S")))
        except OSError:
            self._log_handle = None
        return path

    def _read_output(self) -> None:
        data = self.process.readAllStandardOutput()
        try:
            text = bytes(data).decode("utf-8", errors="replace")
        except Exception:
            return

        # Hold back any trailing partial line: a protocol line split across
        # two reads would fail to parse and be lost.
        self._buffer += text
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()

        for line in lines:
            line = line.rstrip("\r")
            event = protocol.parse(line)
            if event is None:
                # Ordinary Metashape output -- the bulk of it, and what makes
                # the log file readable as a normal processing transcript.
                self._write_log(line)
            else:
                # Protocol lines are not logged: the worker already prints a
                # plain-text equivalent of every event except progress, and
                # logging thousands of progress lines would bury the rest.
                self._handle_event(event)

    def _write_log(self, line: str) -> None:
        if self._log_handle:
            try:
                self._log_handle.write(line + "\n")
                self._log_handle.flush()
            except OSError:
                pass

    def _handle_event(self, event: dict) -> None:
        kind = event.get("ev")
        if kind == protocol.STEP:
            self.job.current_step = event.get("name", "")
            self.job.progress = 0.0
        elif kind == protocol.PROGRESS:
            self.job.progress = float(event.get("pct", 0) or 0)
        elif kind == protocol.WARN:
            message = event.get("msg", "")
            if message and message not in self.job.warnings:
                self.job.warnings.append(message)
        elif kind == protocol.DONE:
            self.job.outputs = event.get("outputs", []) or []
        elif kind == protocol.FAILED:
            self.job.message = event.get("msg", "")


def _worker_script() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "worker", "run_job.py")


class Scheduler(QtCore.QObject):
    """Runs a queue of jobs, up to `parallelism` at a time."""

    jobChanged = Signal(str)      # job_id -- status, progress or step moved
    jobFinished = Signal(str)     # job_id
    batchFinished = Signal()
    message = Signal(str)         # human-readable status for the window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._install: Optional[Install] = None
        self._queue: List[Job] = []
        self._active: Dict[str, JobProcess] = {}
        self._running = False
        self._stopping = False
        self.parallelism = 1
        self.stop_on_error = False

        # Drives elapsed-time display and progress repaints. The jobs
        # themselves push status through signals; this is only so a job that
        # sits in one long stage still shows a ticking clock.
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    # -- control --

    def is_running(self) -> bool:
        return self._running

    def start(self, jobs: List[Job], parallelism: int = 1,
              stop_on_error: bool = False) -> None:
        """Begin running `jobs`. Already-finished jobs are skipped."""
        if self._running:
            return
        try:
            self._install = find_install()
        except MetashapeNotFound as exc:
            self.message.emit(str(exc))
            return

        self.parallelism = max(1, int(parallelism))
        self.stop_on_error = stop_on_error
        self._queue = [j for j in jobs
                       if j.enabled and j.status == models.PENDING]
        self._stopping = False
        self._running = True
        self._timer.start()
        self.message.emit("Running {} job(s), {} at a time.".format(
            len(self._queue), self.parallelism))
        self._fill()

    def stop(self, cancel_running: bool = True) -> None:
        """Stop the batch. Running jobs are cancelled unless asked otherwise.

        `cancel_running=False` is "finish what's started, queue nothing more",
        which is usually what someone wants at the end of a working day.
        """
        self._stopping = True
        self._queue.clear()
        if cancel_running:
            for job_process in list(self._active.values()):
                job_process.cancel()
        else:
            self.message.emit(
                "Finishing {} running job(s); nothing further will start."
                .format(len(self._active)))
        if not self._active:
            self._finish()

    def cancel_job(self, job_id: str) -> None:
        """Cancel one job, leaving the rest of the batch running."""
        job_process = self._active.get(job_id)
        if job_process:
            job_process.cancel()
            return
        # Not started yet: just drop it from the queue.
        for job in list(self._queue):
            if job.job_id == job_id:
                self._queue.remove(job)
                job.status = models.CANCELLED
                self.jobChanged.emit(job_id)
                return

    # -- scheduling --

    def _fill(self) -> None:
        """Start jobs until the parallelism limit or the queue runs out."""
        while (not self._stopping
               and len(self._active) < self.parallelism
               and self._queue):
            job = self._next_startable()
            if job is None:
                break  # everything left is blocked on a busy project
            self._queue.remove(job)
            self._launch(job)

        if not self._active and not self._queue:
            self._finish()

    def _next_startable(self) -> Optional[Job]:
        """The first queued job whose project is not already open elsewhere.

        Metashape locks a project while it has it open, so two workers on one
        .psx is a failure rather than a race worth taking. Re-photography jobs
        for several timepoints of the same plot are a normal thing to queue
        together, which makes this a routine case rather than an edge one:
        they simply run one after another while unrelated plots still go in
        parallel.
        """
        busy = {os.path.normcase(os.path.abspath(p.job.project_path))
                for p in self._active.values() if p.job.project_path}
        for job in self._queue:
            key = os.path.normcase(os.path.abspath(job.project_path or ""))
            if key not in busy:
                return job
        return None

    def _launch(self, job: Job) -> None:
        job_process = JobProcess(job, self._install, self)
        self._active[job.job_id] = job_process
        # *args because QProcess.finished is not the same signal everywhere:
        # PySide2 can deliver just the exit code, while the two-argument
        # (code, exitStatus) overload is what PySide6 sends. Binding to a
        # fixed arity raises TypeError at emit time -- i.e. exactly when a job
        # finishes, leaving it stuck on "running" forever.
        job_process.process.finished.connect(
            lambda *args, jid=job.job_id: self._on_finished(
                jid, args[0] if args else 0))
        job_process.process.errorOccurred.connect(
            lambda err, jid=job.job_id: self._on_error(jid, err))
        job_process.start()
        self.jobChanged.emit(job.job_id)

    def _on_error(self, job_id: str, error) -> None:
        """QProcess-level failure -- the executable would not start at all."""
        job_process = self._active.get(job_id)
        if job_process is None or error != QtCore.QProcess.FailedToStart:
            return
        job_process.job.message = (
            "Could not start Metashape at {}".format(self._install.executable))

    def _on_finished(self, job_id: str, exit_code: int) -> None:
        job_process = self._active.pop(job_id, None)
        if job_process is None:
            return

        job = job_process.job
        result = job_process.cleanup()
        job.finished_at = time.time()
        job.progress = 100.0 if exit_code == 0 else job.progress

        if job_process.cancelled:
            job.status = models.CANCELLED
            job.message = job.message or "Cancelled"
        elif result.get("ok"):
            # The worker distinguishes "finished everything" from "stopped
            # after the mesh because referencing did not work". Both are
            # successful outcomes; only the second needs the user to act.
            if result.get("status") == "stopped_for_manual_referencing":
                job.status = models.NEEDS_REFERENCING
                job.message = ("Stopped after mesh -- referencing needed "
                               "before DEM and exports")
            else:
                job.status = models.DONE
                job.message = ("Finished with {} warning(s)".format(
                    len(job.warnings)) if job.warnings else "Finished")
            job.outputs = result.get("outputs", job.outputs)
            for warning in result.get("warnings", []):
                if warning not in job.warnings:
                    job.warnings.append(warning)
        else:
            job.status = models.FAILED
            job.message = (result.get("error")
                           or job.message
                           or "Metashape exited with code {}".format(exit_code))

        self.jobChanged.emit(job_id)
        self.jobFinished.emit(job_id)

        if job.status == models.FAILED and self.stop_on_error:
            self.message.emit(
                "Stopping: {} failed and 'stop on error' is on."
                .format(job.display_label()))
            self.stop(cancel_running=False)
            return

        self._fill()

    def _tick(self) -> None:
        for job_id in list(self._active):
            self.jobChanged.emit(job_id)

    def _finish(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stopping = False
        self._timer.stop()
        self.batchFinished.emit()
