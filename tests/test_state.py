"""Tests for state management and persistence."""

import json
from pathlib import Path

import pytest

from amul_tracker.parser import StockState
from amul_tracker.state import (
    ProductStateEntry,
    StateCorruptionError,
    StateManager,
    TrackerState,
)


def test_missing_state_file_initializes_empty(tmp_path: Path):
    state_file = tmp_path / "state.json"
    mgr = StateManager(state_file)

    state = mgr.load_state()
    assert state.schema_version == 1
    assert state.consecutive_failures == 0
    assert state.failure_warning_sent is False
    assert state.products == {}


def test_empty_dict_state_file_initializes_empty(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    mgr = StateManager(state_file)

    state = mgr.load_state()
    assert state.schema_version == 1
    assert state.consecutive_failures == 0
    assert state.failure_warning_sent is False
    assert state.products == {}


def test_save_and_load_state(tmp_path: Path):
    state_file = tmp_path / "state.json"
    mgr = StateManager(state_file)

    state = TrackerState(
        schema_version=1,
        last_successful_check="2026-10-15T14:30:00Z",
        consecutive_failures=0,
        failure_warning_sent=False,
        products={
            "amul-whey": ProductStateEntry(state=StockState.OUT_OF_STOCK, checked_at="2026-10-15T14:30:00Z"),
            "amul-lassi": ProductStateEntry(state=StockState.IN_STOCK, checked_at="2026-10-15T14:30:00Z"),
        },
    )

    written = mgr.save_state(state)
    assert written is True
    assert state_file.exists()

    # Load back
    loaded = mgr.load_state()
    assert loaded.schema_version == 1
    assert loaded.last_successful_check == "2026-10-15T14:30:00Z"
    assert len(loaded.products) == 2
    assert loaded.products["amul-whey"].state == StockState.OUT_OF_STOCK
    assert loaded.products["amul-lassi"].state == StockState.IN_STOCK

    # Unchanged state save returns False
    second_save = mgr.save_state(state)
    assert second_save is False


def test_corrupt_json_preserves_corrupt_file_and_raises(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{This is not valid JSON!}")

    mgr = StateManager(state_file)
    with pytest.raises(StateCorruptionError):
        mgr.load_state()

    # Verify backup exists
    backups = list(tmp_path.glob("state.corrupt.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "{This is not valid JSON!}"


def test_unsupported_schema_version_raises(tmp_path: Path):
    state_file = tmp_path / "state.json"
    invalid_schema = {
        "schema_version": 999,
        "consecutive_failures": 0,
        "products": {},
    }
    state_file.write_text(json.dumps(invalid_schema))

    mgr = StateManager(state_file)
    with pytest.raises(StateCorruptionError, match="Unsupported schema version"):
        mgr.load_state()
