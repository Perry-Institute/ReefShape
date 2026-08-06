"""
ReefShape Batch -- a standalone batch processor for the ReefShape underwater
photogrammetry workflow.

Runs outside Metashape. The GUI process never imports the `Metashape` module
(it isn't importable from the bundled interpreter); instead it drives Metashape
by spawning short-lived headless workers:

    metashape.exe -r worker/run_job.py job.json

That split buys crash isolation (a segfault on one plot fails one job, not the
app), real parallelism, and cancellation by process kill. Because the
underlying workflow saves after every stage and guards each stage on a
"is this already done?" check, a killed job resumes where it stopped when
re-run.
"""

__version__ = "0.1.0"
