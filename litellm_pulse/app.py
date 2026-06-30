"""LiteLLM Pulse — a lightweight LiteLLM metrics exporter with SQLite time-series storage.

This module is kept as a thin entry point for backwards compatibility.
The CLI implementation lives in :mod:`litellm_pulse.config`.
"""

from __future__ import annotations

from . import __version__
from .config import main

__all__ = ["__version__", "main"]


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
