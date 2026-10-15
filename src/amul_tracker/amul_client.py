"""Amul StoreHippo API client with session management, location scoping, and retries."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from typing import Any
from urllib.parse import quote

import requests

from amul_tracker.config import LocationConfig, MonitorConfig

logger = logging.getLogger(__name__)

AMUL_BASE_URL = "https://shop.amul.com"
AMUL_STORE_ID = "62fa94df8c13af2e242eba16"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class AmulClientError(Exception):
    """Base exception for Amul client failures."""


class LocationVerificationError(AmulClientError):
    """Raised when pincode lookup or substore resolution fails or mismatches."""


class AmulBlockedError(AmulClientError):
    """Raised when anti-bot or challenge page blocks the request."""


class AmulAuthError(AmulClientError):
    """Raised when session authentication or token calculation fails."""


class AmulClient:
    """Client for interacting with Amul's StoreHippo eCommerce platform."""

    def __init__(
        self,
        location: LocationConfig | None = None,
        monitor: MonitorConfig | None = None,
        session: requests.Session | None = None,
        base_url: str = AMUL_BASE_URL,
    ) -> None:
        self.location = location or LocationConfig()
        self.monitor = monitor or MonitorConfig()
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.server_timestamp: str | None = None
        self.session_tid: str | None = None
        self.active_substore: str | None = None

        # Standard headers
        if hasattr(self.session, "headers") and isinstance(self.session.headers, dict):
            self.session.headers.update({
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            })

    def _execute_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_data: Any = None,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        """Execute HTTP request with bounded retries and exponential backoff."""
        timeout = self.monitor.request_timeout_seconds
        max_retries = self.monitor.max_consecutive_retries
        last_exception: Exception | None = None

        req_headers = headers.copy() if headers else {}

        for attempt in range(max_retries + 1):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    json=json_data,
                    params=params,
                    timeout=timeout,
                    allow_redirects=True,
                )

                # Check for anti-bot challenges
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    lower_text = resp.text.lower()
                    challenge_signatures = [
                        "cf-chl-bypass",
                        "cf-browser-verification",
                        "just a moment...",
                        "attention required! | cloudflare",
                        "challenge-platform",
                        "cf-turnstile",
                        "enable javascript and cookies to continue",
                    ]
                    if any(sig in lower_text for sig in challenge_signatures):
                        raise AmulBlockedError(f"Cloudflare / anti-bot challenge page detected at {url}")

                # Handle HTTP status codes
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    retry_after = resp.headers.get("Retry-After")
                    sleep_time = float(retry_after) if retry_after and retry_after.isdigit() else (2 ** attempt)
                    if attempt < max_retries:
                        logger.warning(
                            "HTTP %s at %s. Retrying in %ss (attempt %s/%s)...",
                            resp.status_code,
                            url,
                            sleep_time,
                            attempt + 1,
                            max_retries,
                        )
                        time.sleep(min(sleep_time, 10))
                        continue

                if resp.status_code in (401, 403):
                    raise AmulAuthError(f"HTTP {resp.status_code} Unauthorized / Forbidden at {url}")

                resp.raise_for_status()
                return resp

            except (requests.RequestException, AmulClientError) as exc:
                last_exception = exc
                if isinstance(exc, (AmulBlockedError, AmulAuthError)):
                    raise
                if attempt < max_retries:
                    sleep_time = 2 ** attempt
                    logger.warning("Request failed (%s). Retrying in %ss...", exc, sleep_time)
                    time.sleep(sleep_time)
                else:
                    break

        raise AmulClientError(f"Request failed after {max_retries + 1} attempts: {last_exception}") from last_exception

    def _generate_auth_tid(self) -> str:
        """Compute the dynamic StoreHippo tid auth token header."""
        if not self.server_timestamp or not self.session_tid:
            raise AmulAuthError("Cannot generate auth tid: server_timestamp or session_tid is missing")

        rand_t = random.randint(0, 999)
        payload = f"{AMUL_STORE_ID}:{self.server_timestamp}:{rand_t}:{self.session_tid}"
        token_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{self.server_timestamp}:{rand_t}:{token_hash}"

    def _get_api_headers(self, referer_path: str = "/en/browse/protein") -> dict[str, str]:
        """Construct standard API request headers with computed tid token."""
        auth_tid = self._generate_auth_tid()
        full_url = f"{self.base_url}{referer_path}"
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": full_url,
            "base_url": full_url,
            "tid": auth_tid,
            "frontend": "1",
        }

    def init_session(self) -> None:
        """Perform initial handshake to obtain cookies, server timestamp, and session tid."""
        logger.info("Initializing Amul StoreHippo session handshake...")
        # Step 1: Fetch browse page
        browse_url = f"{self.base_url}/en/browse/protein"
        resp = self._execute_request(
            "GET",
            browse_url,
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )

        match_ts = re.search(r'serverTimestamp\s*=\s*["\']([^"\']+)["\']', resp.text)
        if not match_ts:
            raise AmulAuthError("Failed to extract serverTimestamp from Amul browse page")
        self.server_timestamp = match_ts.group(1)

        # Step 2: Fetch /user/info.js
        user_info_url = f"{self.base_url}/user/info.js?_v={self.server_timestamp}"
        info_resp = self._execute_request(
            "GET",
            user_info_url,
            headers={"Referer": browse_url},
        )

        match_tid = re.search(r'"tid"\s*:\s*"([^"]+)"', info_resp.text)
        if not match_tid:
            raise AmulAuthError("Failed to extract session tid from user/info.js")
        self.session_tid = match_tid.group(1)
        logger.info("Session handshake successful. Session TID resolved.")

    def set_location(self, pincode: str | None = None, expected_substore: str | None = None) -> str:
        """Lookup pincode and set store preference.
        
        Validates that pincode resolves to the expected substore.
        """
        pincode = pincode or self.location.pincode
        expected_substore = expected_substore or self.location.expected_substore

        if not self.session_tid:
            self.init_session()

        logger.info("Verifying delivery pincode: %s (expected substore: %s)", pincode, expected_substore)

        # Step 1: Pincode lookup
        query = (
            f"limit=50&filters[0][field]=pincode&filters[0][value]={quote(pincode)}"
            f"&filters[0][operator]=regex&filters[0][use_autocomplete]=1&new_search=1&cf_cache=1h"
        )
        pincode_url = f"{self.base_url}/entity/pincode?{query}"
        resp = self._execute_request("GET", pincode_url, headers=self._get_api_headers())

        try:
            pincode_data = resp.json()
        except ValueError as exc:
            raise LocationVerificationError(f"Pincode lookup returned non-JSON: {resp.text[:200]}") from exc

        records = pincode_data.get("records") or []
        matching_record = None
        for rec in records:
            if str(rec.get("pincode")) == str(pincode):
                matching_record = rec
                break

        if not matching_record:
            raise LocationVerificationError(
                f"Pincode '{pincode}' not found in lookup records (count: {len(records)})"
            )

        resolved_substore = matching_record.get("substore")
        if not resolved_substore:
            raise LocationVerificationError(f"Pincode record for '{pincode}' missing substore field: {matching_record}")

        if expected_substore and resolved_substore != expected_substore:
            raise LocationVerificationError(
                f"Location substore mismatch for pincode {pincode}: expected '{expected_substore}', got '{resolved_substore}'"
            )

        # Step 2: Set store preference
        logger.info("Applying store preference for substore: %s", resolved_substore)
        pref_url = f"{self.base_url}/entity/ms.settings/_/setPreferences"
        pref_headers = self._get_api_headers()
        pref_headers["Content-Type"] = "application/json"
        pref_body = {"data": {"store": resolved_substore}}

        pref_resp = self._execute_request(
            "PUT",
            pref_url,
            headers=pref_headers,
            json_data=pref_body,
        )

        if pref_resp.status_code != 200:
            raise LocationVerificationError(
                f"Failed to set store preference for '{resolved_substore}', HTTP status: {pref_resp.status_code}"
            )

        self.active_substore = resolved_substore
        logger.info("Store preference '%s' applied successfully.", resolved_substore)
        return resolved_substore

    def fetch_protein_products(self) -> list[dict[str, Any]]:
        """Fetch all protein category product records via StoreHippo ms.products API."""
        if not self.active_substore:
            self.set_location()

        url = f"{self.base_url}/entity/ms.products?filters[0][field]=categories&filters[0][value]=protein&limit=50"
        logger.info("Fetching protein category products from %s", url)

        resp = self._execute_request("GET", url, headers=self._get_api_headers())

        try:
            data = resp.json()
        except ValueError as exc:
            raise AmulClientError(f"Product API returned invalid JSON: {resp.text[:200]}") from exc

        records = data.get("records")
        if records is None or not isinstance(records, list):
            raise AmulClientError(f"Unexpected response structure: 'records' field missing or invalid ({list(data.keys())})")

        logger.info("Fetched %d product records from Amul API (total reported: %s)", len(records), data.get("total"))
        return records

    def fetch_product_by_alias(self, alias: str) -> dict[str, Any] | None:
        """Fetch a specific product record by alias/slug."""
        if not self.active_substore:
            self.set_location()

        url = f"{self.base_url}/entity/ms.products?filters[0][field]=alias&filters[0][value]={quote(alias)}&limit=1"
        resp = self._execute_request("GET", url, headers=self._get_api_headers())

        try:
            data = resp.json()
        except ValueError as exc:
            raise AmulClientError(f"Product API for alias '{alias}' returned invalid JSON") from exc

        records = data.get("records") or []
        return records[0] if records else None
