"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .watcher import check_products


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Watch product prices and send Telegram alerts.")
    result.add_argument("--config", type=Path, default=Path("watchlist.json"))
    result.add_argument("--state", type=Path, default=Path("price_state.json"))
    result.add_argument("--interval-hours", type=float, default=4)
    result.add_argument("--once", action="store_true", help="Check once and exit")
    result.add_argument("--log-file", type=Path, default=Path("price-watcher.log"))
    return result


def main() -> int:
    args = parser().parse_args()
    config = args.config.expanduser().resolve()
    if not config.is_file():
        raise SystemExit(f"Configuration file does not exist: {config}")
    if args.interval_hours <= 0:
        raise SystemExit("--interval-hours must be greater than zero.")
    logging.basicConfig(filename=args.log_file, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        while True:
            check_products(config, args.state.expanduser().resolve())
            if args.once:
                break
            time.sleep(args.interval_hours * 3600)
    except KeyboardInterrupt:
        print("\nStopped.")
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
