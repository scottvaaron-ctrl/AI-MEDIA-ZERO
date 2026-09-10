"""Entry point for `python -m aimz`.

The scheduled task launches the CLI this way rather than through the
aimz.exe shim in the venv Scripts folder: that shim is unsigned and Windows
Smart App Control blocks it, which killed every scheduled cycle without
leaving a trace.
"""

from __future__ import annotations

from aimz.cli import app

if __name__ == "__main__":
    app()
