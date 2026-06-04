#!/usr/bin/env python3
"""Launcher (also the PyInstaller entry point). `python run.py [--selftest]`."""
import os
import sys


def _safe_stream(name: str) -> None:
    """Keep printing from crashing on Windows: a --windowed build has no stdout/stderr (-> null),
    and a legacy console uses cp1252 which can't encode ★/▶ etc. -> force UTF-8 + replace."""
    s = getattr(sys, name, None)
    if s is None:
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
    else:
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — stream may not support reconfigure; leave it
            pass


_safe_stream("stdout")
_safe_stream("stderr")

from streamtuner_ng.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
