"""Tests for AmulClient HTTP communication, location verification, and retries."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from amul_tracker.amul_client import (
    AmulAuthError,
    AmulBlockedError,
    AmulClient,
    AmulClientError,
    LocationVerificationError,
)
from amul_tracker.config import LocationConfig, MonitorConfig


@pytest.fixture
def mock_session():
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    return session


def test_init_session_extracts_tokens(mock_session):
    client = AmulClient(session=mock_session)

    # Browse response
    resp_browse = MagicMock()
    resp_browse.status_code = 200
    resp_browse.headers = {"Content-Type": "text/html"}
    resp_browse.text = '<html><script>serverTimestamp = "1792075068907"</script></html>'

    # User info response
    resp_info = MagicMock()
    resp_info.status_code = 200
    resp_info.headers = {"Content-Type": "application/javascript"}
    resp_info.text = 'session = {"data":{},"tid":"test_tid_123"}'

    mock_session.request.side_effect = [resp_browse, resp_info]

    client.init_session()
    assert client.server_timestamp == "1792075068907"
    assert client.session_tid == "test_tid_123"


def test_location_substore_mismatch_raises(mock_session):
    client = AmulClient(
        location=LocationConfig(pincode="411036", expected_substore="pune-br"),
        session=mock_session,
    )
    client.server_timestamp = "123456"
    client.session_tid = "tid_abc"

    # Pincode lookup response returning delhi-br instead of pune-br
    resp_pin = MagicMock()
    resp_pin.status_code = 200
    resp_pin.headers = {"Content-Type": "application/json"}
    resp_pin.json.return_value = {
        "records": [{"pincode": "411036", "substore": "delhi-br"}]
    }

    mock_session.request.return_value = resp_pin

    with pytest.raises(LocationVerificationError, match="substore mismatch"):
        client.set_location("411036", "pune-br")


def test_location_success_and_preference_set(mock_session):
    client = AmulClient(
        location=LocationConfig(pincode="411036", expected_substore="pune-br"),
        session=mock_session,
    )
    client.server_timestamp = "123456"
    client.session_tid = "tid_abc"

    resp_pin = MagicMock()
    resp_pin.status_code = 200
    resp_pin.headers = {"Content-Type": "application/json"}
    resp_pin.json.return_value = {
        "records": [{"pincode": "411036", "substore": "pune-br"}]
    }

    resp_pref = MagicMock()
    resp_pref.status_code = 200
    resp_pref.headers = {"Content-Type": "application/json"}
    resp_pref.text = "Updated successfully"

    mock_session.request.side_effect = [resp_pin, resp_pref]

    substore = client.set_location("411036", "pune-br")
    assert substore == "pune-br"
    assert client.active_substore == "pune-br"


def test_challenge_page_raises_amul_blocked_error(mock_session):
    client = AmulClient(session=mock_session)

    resp_challenge = MagicMock()
    resp_challenge.status_code = 200
    resp_challenge.headers = {"Content-Type": "text/html"}
    resp_challenge.text = "<html><title>Just a moment...</title><div id='cf-turnstile'></div></html>"

    mock_session.request.return_value = resp_challenge

    with pytest.raises(AmulBlockedError, match="challenge page detected"):
        client.init_session()


def test_unauthorized_raises_amul_auth_error(mock_session):
    client = AmulClient(session=mock_session)

    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.headers = {}
    mock_session.request.return_value = resp_401

    with pytest.raises(AmulAuthError, match="HTTP 401"):
        client.init_session()


def test_retries_on_500_error(mock_session):
    monitor_cfg = MonitorConfig(request_timeout_seconds=5, max_consecutive_retries=2)
    client = AmulClient(monitor=monitor_cfg, session=mock_session)

    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.headers = {}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.headers = {"Content-Type": "text/html"}
    resp_200.text = '<script>serverTimestamp = "123"</script>'

    resp_info = MagicMock()
    resp_info.status_code = 200
    resp_info.headers = {}
    resp_info.text = 'session = {"tid": "tid_123"}'

    # First request fails once with 500, then succeeds on retry
    mock_session.request.side_effect = [resp_500, resp_200, resp_info]

    with patch("time.sleep"):
        client.init_session()

    assert client.server_timestamp == "123"
    assert client.session_tid == "tid_123"
