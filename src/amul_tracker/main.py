"""CLI entrypoint for Amul Protein Tracker."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from amul_tracker.amul_client import AmulClient
from amul_tracker.checker import StockChecker
from amul_tracker.config import ConfigError, load_config
from amul_tracker.parser import parse_json_product_record
from amul_tracker.state import StateManager
from amul_tracker.telegram import TelegramNotifier


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def verify_live_connection(config_path: str | None = None) -> int:
    """Diagnostic tool to inspect live Amul inventory without modifying state."""
    print("=== Amul Protein Tracker: Diagnostic / Live Verification ===")
    config = load_config(config_path=config_path, require_telegram=False)
    print(f"Configured Pincode: {config.location.pincode}")
    print(f"Expected Substore: {config.location.expected_substore}")
    print(f"Configured Products: {len(config.products)} ({len(config.get_enabled_products())} enabled)\n")

    client = AmulClient(location=config.location, monitor=config.monitor)
    try:
        substore = client.set_location()
        print(f"✅ Location verified: substore '{substore}'")

        records = client.fetch_protein_products()
        print(f"✅ Fetched {len(records)} product records from StoreHippo API\n")

        records_by_slug = {r.get("alias"): r for r in records if r.get("alias")}

        print("--- Configured Products Evaluation ---")
        for p in config.get_enabled_products():
            rec = records_by_slug.get(p.slug)
            if not rec:
                print(f"❌ {p.name} ({p.slug}): NOT FOUND in category response")
                continue
            res = parse_json_product_record(rec, expected_slug=p.slug)
            print(f"📦 {p.name}")
            print(f"   Slug: {p.slug}")
            print(f"   Stock State: {res.state.value}")
            print(f"   Reason: {res.reason}")
            print(f"   Inventory Qty: {res.inventory_quantity}\n")

        return 0

    except Exception as exc:
        print(f"❌ Live verification failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amul Protein Stock Monitor")
    parser.add_argument("-c", "--config", default=None, help="Path to products.yaml config file")
    parser.add_argument("-s", "--state", default=None, help="Path to state.json file")
    parser.add_argument("-e", "--env", default=None, help="Path to .env file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Run live verification diagnostic without sending alerts or persisting state",
    )

    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)

    if args.verify_only:
        return verify_live_connection(config_path=args.config)

    try:
        config = load_config(
            config_path=args.config,
            env_file=args.env,
            require_telegram=False,
        )
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        return 1

    state_path = Path(args.state) if args.state else config.state_file_path
    state_manager = StateManager(state_path)
    client = AmulClient(location=config.location, monitor=config.monitor)
    notifier = TelegramNotifier(config.telegram)

    checker = StockChecker(
        config=config,
        state_manager=state_manager,
        client=client,
        notifier=notifier,
    )

    return checker.run()


if __name__ == "__main__":
    sys.exit(main())
