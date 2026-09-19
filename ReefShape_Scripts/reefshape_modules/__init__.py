"""
Shared ReefShape code, imported by the menu scripts and the batch worker.

This package is deliberately not called `modules`. Metashape ships its own
top-level `modules` package (the Console's backend, plus its own
pip_auto_install) and imports it at startup, before any user script runs.
Python keeps one module per name, so `from modules import reefshape_core`
searches Metashape's package, never ours, and fails with "cannot import name".
A name Metashape does not use avoids the clash; ReefShape Air does the same
with `reefshape_air_modules`.
"""
