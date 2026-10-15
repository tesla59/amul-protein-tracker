"""Tests for StockChecker orchestration, transition logic, failure tracking, and persistence."""

from unittest.mock import MagicMock

import pytest

from amul_tracker.amul_client import LocationVerificationError
from amul_tracker.checker import StockChecker
from amul_tracker.config import (
    AppConfig,
    LocationConfig,
    MonitorConfig,
    ProductConfig,
    TelegramConfig,
)
from amul_tracker.parser import StockState
from amul_tracker.state import ProductStateEntry, StateManager, TrackerState


@pytest.fixture
def base_config(tmp_path) -> AppConfig:
    state_file = tmp_path / "state.json"
    return AppConfig(
        location=LocationConfig(pincode="411036", expected_substore="pune-br"),
        monitor=MonitorConfig(failure_warning_threshold=3, request_timeout_seconds=5, max_consecutive_retries=1),
        products=[
            ProductConfig(
                id="amul-whey",
                name="Amul Whey Protein",
                url="https://shop.amul.com/product/amul-whey",
                enabled=True,
            ),
            ProductConfig(
                id="amul-lassi",
                name="Amul High Protein Lassi",
                url="https://shop.amul.com/product/amul-lassi",
                enabled=True,
            ),
        ],
        telegram=TelegramConfig(bot_token="fake_token", chat_id="fake_chat"),
        state_file_path=state_file,
    )


def test_first_run_in_stock_and_out_of_stock_behavior(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    mock_client = MagicMock()
    mock_notifier = MagicMock()

    # Client returns whey as OUT_OF_STOCK (avail=0), lassi as IN_STOCK (avail=1)
    mock_client.fetch_protein_products.return_value = [
        {"alias": "amul-whey", "name": "Amul Whey Protein", "available": 0, "inventory_quantity": 0},
        {"alias": "amul-lassi", "name": "Amul High Protein Lassi", "available": 1, "inventory_quantity": 50},
    ]

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)
    exit_code = checker.run()

    assert exit_code == 0
    # First run: in-stock sends 1 alert; out-of-stock sends NO alert
    assert mock_notifier.notify_stock_transition.call_count == 1
    call_args = mock_notifier.notify_stock_transition.call_args[1]
    assert call_args["product_name"] == "Amul High Protein Lassi"
    assert call_args["new_state"] == StockState.IN_STOCK
    assert call_args["is_first_run"] is True

    # State persists both states
    saved_state = state_mgr.load_state()
    assert saved_state.products["amul-whey"].state == StockState.OUT_OF_STOCK
    assert saved_state.products["amul-lassi"].state == StockState.IN_STOCK
    assert saved_state.consecutive_failures == 0


def test_no_notification_when_stock_unchanged(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    initial_state = TrackerState(
        schema_version=1,
        last_successful_check="2026-10-15T12:00:00Z",
        consecutive_failures=0,
        products={
            "amul-whey": ProductStateEntry(state=StockState.OUT_OF_STOCK, checked_at="2026-10-15T12:00:00Z"),
            "amul-lassi": ProductStateEntry(state=StockState.IN_STOCK, checked_at="2026-10-15T12:00:00Z"),
        },
    )
    state_mgr.save_state(initial_state)

    mock_client = MagicMock()
    mock_notifier = MagicMock()
    mock_client.fetch_protein_products.return_value = [
        {"alias": "amul-whey", "name": "Amul Whey Protein", "available": 0, "inventory_quantity": 0},
        {"alias": "amul-lassi", "name": "Amul High Protein Lassi", "available": 1, "inventory_quantity": 50},
    ]

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)
    exit_code = checker.run()

    assert exit_code == 0
    # Unchanged state: exactly 0 transition alerts
    mock_notifier.notify_stock_transition.assert_not_called()


def test_transition_out_of_stock_to_in_stock(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    initial_state = TrackerState(
        schema_version=1,
        products={
            "amul-whey": ProductStateEntry(state=StockState.OUT_OF_STOCK, checked_at="2026-10-15T12:00:00Z"),
            "amul-lassi": ProductStateEntry(state=StockState.IN_STOCK, checked_at="2026-10-15T12:00:00Z"),
        },
    )
    state_mgr.save_state(initial_state)

    mock_client = MagicMock()
    mock_notifier = MagicMock()
    # Now whey is back in stock!
    mock_client.fetch_protein_products.return_value = [
        {"alias": "amul-whey", "name": "Amul Whey Protein", "available": 1, "inventory_quantity": 25},
        {"alias": "amul-lassi", "name": "Amul High Protein Lassi", "available": 1, "inventory_quantity": 50},
    ]

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)
    exit_code = checker.run()

    assert exit_code == 0
    assert mock_notifier.notify_stock_transition.call_count == 1
    call_args = mock_notifier.notify_stock_transition.call_args[1]
    assert call_args["product_name"] == "Amul Whey Protein"
    assert call_args["old_state"] == StockState.OUT_OF_STOCK
    assert call_args["new_state"] == StockState.IN_STOCK

    # Saved state updated
    saved_state = state_mgr.load_state()
    assert saved_state.products["amul-whey"].state == StockState.IN_STOCK


def test_transition_in_stock_to_out_of_stock(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    initial_state = TrackerState(
        schema_version=1,
        products={
            "amul-whey": ProductStateEntry(state=StockState.IN_STOCK, checked_at="2026-10-15T12:00:00Z"),
            "amul-lassi": ProductStateEntry(state=StockState.IN_STOCK, checked_at="2026-10-15T12:00:00Z"),
        },
    )
    state_mgr.save_state(initial_state)

    mock_client = MagicMock()
    mock_notifier = MagicMock()
    # Now whey sold out
    mock_client.fetch_protein_products.return_value = [
        {"alias": "amul-whey", "name": "Amul Whey Protein", "available": 0, "inventory_quantity": 0},
        {"alias": "amul-lassi", "name": "Amul High Protein Lassi", "available": 1, "inventory_quantity": 50},
    ]

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)
    exit_code = checker.run()

    assert exit_code == 0
    assert mock_notifier.notify_stock_transition.call_count == 1
    call_args = mock_notifier.notify_stock_transition.call_args[1]
    assert call_args["product_name"] == "Amul Whey Protein"
    assert call_args["old_state"] == StockState.IN_STOCK
    assert call_args["new_state"] == StockState.OUT_OF_STOCK


def test_unknown_does_not_overwrite_last_known_stock_state_and_no_false_oos(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    initial_state = TrackerState(
        schema_version=1,
        products={
            "amul-whey": ProductStateEntry(state=StockState.IN_STOCK, checked_at="2026-10-15T12:00:00Z"),
            "amul-lassi": ProductStateEntry(state=StockState.IN_STOCK, checked_at="2026-10-15T12:00:00Z"),
        },
    )
    state_mgr.save_state(initial_state)

    mock_client = MagicMock()
    mock_notifier = MagicMock()
    # Client returns an empty list (failed or incomplete scrape)
    mock_client.fetch_protein_products.return_value = []

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)
    exit_code = checker.run()

    assert exit_code == 1
    # NO transition alerts sent (especially no false out of stock notification!)
    mock_notifier.notify_stock_transition.assert_not_called()

    # Prior stock states preserved intact
    saved_state = state_mgr.load_state()
    assert saved_state.products["amul-whey"].state == StockState.IN_STOCK
    assert saved_state.products["amul-lassi"].state == StockState.IN_STOCK
    assert saved_state.consecutive_failures == 1


def test_failure_counter_escalation_and_threshold_warning(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    mock_client = MagicMock()
    mock_notifier = MagicMock()
    mock_client.fetch_protein_products.side_effect = Exception("Amul connection timeout")

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)

    # Run 1: failure 1, no warning
    checker.run()
    assert state_mgr.load_state().consecutive_failures == 1
    mock_notifier.notify_failure_warning.assert_not_called()

    # Run 2: failure 2, no warning
    checker.run()
    assert state_mgr.load_state().consecutive_failures == 2
    mock_notifier.notify_failure_warning.assert_not_called()

    # Run 3: failure 3 (threshold reached!) -> send failure warning once
    checker.run()
    assert state_mgr.load_state().consecutive_failures == 3
    assert state_mgr.load_state().failure_warning_sent is True
    assert mock_notifier.notify_failure_warning.call_count == 1

    # Run 4: failure 4 -> should NOT send duplicate warning
    checker.run()
    assert state_mgr.load_state().consecutive_failures == 4
    assert mock_notifier.notify_failure_warning.call_count == 1


def test_recovery_notification_sent_after_failure_warning(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    # Start with active failure warning
    initial_state = TrackerState(
        schema_version=1,
        consecutive_failures=3,
        failure_warning_sent=True,
        products={},
    )
    state_mgr.save_state(initial_state)

    mock_client = MagicMock()
    mock_notifier = MagicMock()
    mock_client.fetch_protein_products.return_value = [
        {"alias": "amul-whey", "name": "Amul Whey Protein", "available": 0, "inventory_quantity": 0},
        {"alias": "amul-lassi", "name": "Amul High Protein Lassi", "available": 1, "inventory_quantity": 50},
    ]

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)
    exit_code = checker.run()

    assert exit_code == 0
    # Recovery alert sent
    assert mock_notifier.notify_recovery.call_count == 1
    # Counter reset
    saved_state = state_mgr.load_state()
    assert saved_state.consecutive_failures == 0
    assert saved_state.failure_warning_sent is False
    assert saved_state.last_successful_check is not None


def test_location_mismatch_prevents_stock_state_updates(base_config):
    state_mgr = StateManager(base_config.state_file_path)
    initial_state = TrackerState(
        schema_version=1,
        products={
            "amul-whey": ProductStateEntry(state=StockState.OUT_OF_STOCK, checked_at="2026-10-15T12:00:00Z"),
        },
    )
    state_mgr.save_state(initial_state)

    mock_client = MagicMock()
    mock_notifier = MagicMock()
    mock_client.fetch_protein_products.side_effect = LocationVerificationError("Expected pune-br, got delhi-br")

    checker = StockChecker(base_config, state_manager=state_mgr, client=mock_client, notifier=mock_notifier)
    exit_code = checker.run()

    assert exit_code == 1
    # No stock alerts
    mock_notifier.notify_stock_transition.assert_not_called()
    # State preserved
    saved_state = state_mgr.load_state()
    assert saved_state.products["amul-whey"].state == StockState.OUT_OF_STOCK
    assert saved_state.consecutive_failures == 1
