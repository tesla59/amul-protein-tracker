"""State persistence for Amul Protein Tracker.

Manages schema-versioned state tracking product stock states, check timestamps,
and consecutive failures.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from amul_tracker.parser import StockState

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class StateCorruptionError(Exception):
    """Raised when the state file contains invalid JSON or unsupported schema."""


@dataclass
class ProductStateEntry:
    state: StockState
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductStateEntry:
        state_str = data.get("state", StockState.UNKNOWN.value)
        try:
            state_enum = StockState(state_str)
        except ValueError:
            state_enum = StockState.UNKNOWN

        return cls(
            state=state_enum,
            checked_at=str(data.get("checked_at", "")),
        )


@dataclass
class TrackerState:
    schema_version: int = SCHEMA_VERSION
    last_successful_check: str | None = None
    consecutive_failures: int = 0
    failure_warning_sent: bool = False
    products: dict[str, ProductStateEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "last_successful_check": self.last_successful_check,
            "consecutive_failures": self.consecutive_failures,
            "failure_warning_sent": self.failure_warning_sent,
            "products": {slug: entry.to_dict() for slug, entry in sorted(self.products.items())},
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackerState:
        if not isinstance(data, dict):
            raise StateCorruptionError("State data root must be a dictionary")

        schema_ver = data.get("schema_version")
        if schema_ver != SCHEMA_VERSION:
            raise StateCorruptionError(f"Unsupported schema version: {schema_ver} (expected {SCHEMA_VERSION})")

        last_check = data.get("last_successful_check")
        consecutive_failures = int(data.get("consecutive_failures", 0))
        warning_sent = bool(data.get("failure_warning_sent", False))

        raw_products = data.get("products", {})
        if not isinstance(raw_products, dict):
            raise StateCorruptionError("'products' field must be a dictionary")

        products: dict[str, ProductStateEntry] = {}
        for slug, p_data in raw_products.items():
            if isinstance(p_data, dict):
                products[slug] = ProductStateEntry.from_dict(p_data)

        return cls(
            schema_version=schema_ver,
            last_successful_check=last_check,
            consecutive_failures=consecutive_failures,
            failure_warning_sent=warning_sent,
            products=products,
        )


class StateManager:
    """Handles loading, updating, and saving state to disk safely and atomically."""

    def __init__(self, state_file_path: str | Path = "state.json") -> None:
        self.state_file_path = Path(state_file_path)
        self._initial_json_content: str | None = None

    def load_state(self) -> TrackerState:
        """Load state from file, or return empty default state if missing."""
        if not self.state_file_path.exists():
            logger.info("No existing state file found at %s. Initializing fresh state.", self.state_file_path)
            state = TrackerState()
            self._initial_json_content = state.to_json()
            return state

        try:
            content = self.state_file_path.read_text(encoding="utf-8")
            self._initial_json_content = content
            if not content.strip():
                logger.warning("State file %s is empty. Initializing fresh state.", self.state_file_path)
                return TrackerState()

            data = json.loads(content)
            return TrackerState.from_dict(data)

        except (json.JSONDecodeError, StateCorruptionError) as exc:
            # Preserve corrupted file by backing it up
            backup_path = self.state_file_path.with_suffix(f".corrupt.{int(datetime.now().timestamp())}")
            try:
                shutil.copy(self.state_file_path, backup_path)
                logger.error("State file corrupted: %s. Backed up to %s", exc, backup_path)
            except Exception as copy_err:
                logger.error("Failed to backup corrupt state file: %s", copy_err)

            raise StateCorruptionError(f"Corrupt state file: {exc}. Preserved copy at {backup_path}") from exc

    def save_state(self, state: TrackerState, force: bool = False) -> bool:
        """Save state to file atomically. Returns True if file was written, False if unchanged."""
        new_content = state.to_json()
        if not force and self._initial_json_content is not None and new_content == self._initial_json_content:
            logger.debug("State has not changed. Skipping write.")
            return False

        # Atomic write via temporary file
        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file_path.with_suffix(".tmp")
        try:
            temp_file.write_text(new_content, encoding="utf-8")
            temp_file.replace(self.state_file_path)
            self._initial_json_content = new_content
            logger.info("Persisted updated state to %s", self.state_file_path)
            return True
        except Exception:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise
