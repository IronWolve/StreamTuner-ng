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
    ap.add_argument("--network", action="store_true", help="include live directory fetches in --selftest")
    ap.add_argument("--gui-smoke", action="store_true", help="include isolated offscreen Qt checks in --selftest")
    ap.add_argument("--engine-check", action="store_true", help="initialize the audio engine with null output in --selftest")
    ap.add_argument("--selftest-report", metavar="FILE", help="write the selftest result as JSON")
    args = ap.parse_args(argv)
    if (args.network or args.gui_smoke or args.engine_check or args.selftest_report) and not args.selftest:
        ap.error("verification options require --selftest")

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.selftest:
        from .selftest import run_selftest
        return run_selftest(network=args.network, gui=args.gui_smoke, engine=args.engine_check, report=args.selftest_report)

    # default: launch the GUI
    from .ui.app import run_app
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
