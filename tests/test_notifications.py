"""Tests for Telegram notifications."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from amul_tracker.config import TelegramConfig
from amul_tracker.parser import StockState
from amul_tracker.telegram import TelegramNotifier


def test_telegram_not_configured_skips():
    cfg = TelegramConfig(bot_token=None, chat_id=None)
    mock_session = MagicMock()
    notifier = TelegramNotifier(cfg, session=mock_session)

    res = notifier.send_message("Hello")
    assert res is False
    mock_session.post.assert_not_called()


def test_first_run_in_stock_notification():
    cfg = TelegramConfig(bot_token="test_token", chat_id="12345")
    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_session.post.return_value = mock_resp

    notifier = TelegramNotifier(cfg, session=mock_session)
    check_time = datetime(2026, 10, 15, 14, 30, tzinfo=timezone.utc)

    res = notifier.notify_stock_transition(
        product_name="Amul High Protein Buttermilk",
        product_url="https://shop.amul.com/product/amul-high-protein-buttermilk",
        old_state=None,
        new_state=StockState.IN_STOCK,
        pincode="411036",
        check_time=check_time,
        is_first_run=True,
    )

    assert res is True
    assert mock_session.post.call_count == 1
    call_args = mock_session.post.call_args
    assert "https://api.telegram.org/bottest_token/sendMessage" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["chat_id"] == "12345"
    text = payload["text"]
    assert "Amul High Protein Buttermilk" in text
    assert "411036" in text
    assert "Currently In Stock" in text
    assert "IST" in text
    assert "https://shop.amul.com/product/amul-high-protein-buttermilk" in text


def test_back_in_stock_notification():
    cfg = TelegramConfig(bot_token="test_token", chat_id="12345")
    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_session.post.return_value = mock_resp

    notifier = TelegramNotifier(cfg, session=mock_session)

    res = notifier.notify_stock_transition(
        product_name="Amul Whey Protein",
        product_url="https://shop.amul.com/product/amul-whey",
        old_state=StockState.OUT_OF_STOCK,
        new_state=StockState.IN_STOCK,
        pincode="411036",
    )

    assert res is True
    payload = mock_session.post.call_args[1]["json"]
    text = payload["text"]
    assert "Back In Stock!" in text
    assert "Sold Out" in text


def test_sold_out_notification():
    cfg = TelegramConfig(bot_token="test_token", chat_id="12345")
    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_session.post.return_value = mock_resp

    notifier = TelegramNotifier(cfg, session=mock_session)

    res = notifier.notify_stock_transition(
        product_name="Amul Whey Protein",
        product_url="https://shop.amul.com/product/amul-whey",
        old_state=StockState.IN_STOCK,
        new_state=StockState.OUT_OF_STOCK,
        pincode="411036",
    )

    assert res is True
    payload = mock_session.post.call_args[1]["json"]
    text = payload["text"]
    assert "Sold Out" in text


def test_failure_warning_notification():
    cfg = TelegramConfig(bot_token="test_token", chat_id="12345")
    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_session.post.return_value = mock_resp

    notifier = TelegramNotifier(cfg, session=mock_session)

    res = notifier.notify_failure_warning(
        consecutive_failures=3,
        threshold=3,
        last_error_message="HTTP 502 Bad Gateway",
        pincode="411036",
    )

    assert res is True
    payload = mock_session.post.call_args[1]["json"]
    text = payload["text"]
    assert "Monitoring Alert" in text
    assert "3" in text
    assert "HTTP 502 Bad Gateway" in text


def test_recovery_notification():
    cfg = TelegramConfig(bot_token="test_token", chat_id="12345")
    mock_session = MagicMock()
    mock_resp = MagicMock(status_code=200)
    mock_session.post.return_value = mock_resp

    notifier = TelegramNotifier(cfg, session=mock_session)

    res = notifier.notify_recovery(
        resolved_after_failures=3,
        pincode="411036",
    )

    assert res is True
    payload = mock_session.post.call_args[1]["json"]
    text = payload["text"]
    assert "Service Recovered" in text
    assert "3" in text
