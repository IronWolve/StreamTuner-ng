"""Entry point: `python -m streamtuner_ng [--selftest|--version]`.

Keeps imports lazy so --selftest/--version work even where the GUI stack (Qt,
libmpv) is unavailable (e.g. headless WSL).
"""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="streamtuner-ng", description=f"{APP_NAME} — internet radio browser")
    ap.add_argument("--version", action="store_true", help="print version and exit")
    ap.add_argument("--selftest", action="store_true", help="run headless self-check and exit")
    args = ap.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.selftest:
        from .selftest import run_selftest
        return run_selftest()

    # default: launch the GUI
    from .ui.app import run_app
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
