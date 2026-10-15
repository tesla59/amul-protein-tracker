"""Core stock checker orchestration for Amul Protein Tracker."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from amul_tracker.amul_client import AmulClient, AmulClientError
from amul_tracker.config import AppConfig
from amul_tracker.parser import StockResult, StockState, parse_json_product_record
from amul_tracker.state import ProductStateEntry, StateManager, TrackerState
from amul_tracker.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


class StockChecker:
    """Orchestrates stock scraping, state evaluation, transitions, and alerts."""

    def __init__(
        self,
        config: AppConfig,
        state_manager: StateManager | None = None,
        client: AmulClient | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.config = config
        self.state_manager = state_manager or StateManager(config.state_file_path)
        self.client = client or AmulClient(location=config.location, monitor=config.monitor)
        self.notifier = notifier or TelegramNotifier(config.telegram)

    def run(self) -> int:
        """Execute a single check cycle.
        
        Returns:
            0 on success, 1 on check failure.
        """
        check_time = datetime.now(timezone.utc)
        iso_now = check_time.isoformat()

        logger.info("Starting stock check cycle at %s...", iso_now)
        state = self.state_manager.load_state()

        enabled_products = self.config.get_enabled_products()
        if not enabled_products:
            logger.warning("No enabled products configured in products.yaml")
            return 0

        scrape_error: Exception | None = None
        records_by_alias: dict[str, dict[str, Any]] = {}

        try:
            raw_records = self.client.fetch_protein_products()
            for rec in raw_records:
                alias = rec.get("alias")
                if alias:
                    records_by_alias[alias] = rec
        except Exception as exc:
            scrape_error = exc
            logger.error("Failed to fetch product records from Amul: %s", exc)

        has_product_failure = False
        product_failure_reasons: list[str] = []

        if scrape_error is not None:
            has_product_failure = True
            product_failure_reasons.append(str(scrape_error))
        else:
            # Process each enabled product
            for product in enabled_products:
                slug = product.slug
                rec = records_by_alias.get(slug)

                if rec is None:
                    # Specific product was not found in category response
                    logger.warning("Product '%s' (slug: %s) not found in category response", product.name, slug)
                    has_product_failure = True
                    product_failure_reasons.append(f"Product '{product.name}' ({slug}) missing in Amul response")
                    continue

                parse_result = parse_json_product_record(rec, expected_slug=slug)
                logger.info(
                    "Evaluated product '%s' (%s): %s (%s)",
                    product.name,
                    slug,
                    parse_result.state.value,
                    parse_result.reason,
                )

                if parse_result.state == StockState.UNKNOWN:
                    has_product_failure = True
                    product_failure_reasons.append(f"Product '{product.name}' returned UNKNOWN: {parse_result.reason}")
                    # NEVER overwrite valid prior state with UNKNOWN
                    continue

                prior_entry = state.products.get(slug)
                old_state = prior_entry.state if prior_entry else None
                new_state = parse_result.state

                # Check transitions
                if prior_entry is None:
                    # First run
                    logger.info("First run for product '%s' (%s). State: %s", product.name, slug, new_state.value)
                    if new_state == StockState.IN_STOCK:
                        try:
                            self.notifier.notify_stock_transition(
                                product_name=product.name,
                                product_url=product.url,
                                old_state=None,
                                new_state=new_state,
                                pincode=self.config.location.pincode,
                                check_time=check_time,
                                is_first_run=True,
                            )
                        except Exception as notif_err:
                            logger.error("Failed to send first-run notification for '%s': %s", product.name, notif_err)
                            has_product_failure = True
                            product_failure_reasons.append(f"Telegram delivery failed for '{product.name}': {notif_err}")
                    # Persist known state
                    state.products[slug] = ProductStateEntry(state=new_state, checked_at=iso_now)

                else:
                    # Subsequent run
                    if old_state != new_state:
                        logger.info(
                            "Stock transition detected for '%s' (%s): %s -> %s",
                            product.name,
                            slug,
                            old_state.value if old_state else "None",
                            new_state.value,
                        )
                        try:
                            self.notifier.notify_stock_transition(
                                product_name=product.name,
                                product_url=product.url,
                                old_state=old_state,
                                new_state=new_state,
                                pincode=self.config.location.pincode,
                                check_time=check_time,
                                is_first_run=False,
                            )
                        except Exception as notif_err:
                            logger.error("Failed to send transition notification for '%s': %s", product.name, notif_err)
                            has_product_failure = True
                            product_failure_reasons.append(f"Telegram delivery failed for '{product.name}': {notif_err}")
                        state.products[slug] = ProductStateEntry(state=new_state, checked_at=iso_now)
                    else:
                        # State unchanged
                        logger.debug("Product '%s' (%s) state unchanged: %s", product.name, slug, new_state.value)
                        state.products[slug] = ProductStateEntry(state=new_state, checked_at=iso_now)

        # Handle health, failure escalation, and recovery
        if has_product_failure:
            state.consecutive_failures += 1
            combined_reason = "; ".join(product_failure_reasons)
            logger.warning(
                "Checker failure count incremented to %d (threshold: %d). Reason: %s",
                state.consecutive_failures,
                self.config.monitor.failure_warning_threshold,
                combined_reason,
            )

            if (
                state.consecutive_failures >= self.config.monitor.failure_warning_threshold
                and not state.failure_warning_sent
            ):
                try:
                    self.notifier.notify_failure_warning(
                        consecutive_failures=state.consecutive_failures,
                        threshold=self.config.monitor.failure_warning_threshold,
                        last_error_message=combined_reason,
                        pincode=self.config.location.pincode,
                        check_time=check_time,
                    )
                    state.failure_warning_sent = True
                except Exception as notif_err:
                    logger.error("Failed to send failure warning: %s", notif_err)

            self.state_manager.save_state(state)
            return 1

        else:
            # Complete check success
            if state.failure_warning_sent:
                try:
                    self.notifier.notify_recovery(
                        resolved_after_failures=state.consecutive_failures,
                        pincode=self.config.location.pincode,
                        check_time=check_time,
                    )
                except Exception as notif_err:
                    logger.error("Failed to send recovery notification: %s", notif_err)

            state.consecutive_failures = 0
            state.failure_warning_sent = False
            state.last_successful_check = iso_now

            self.state_manager.save_state(state)
            logger.info("Stock check completed successfully.")
            return 0
