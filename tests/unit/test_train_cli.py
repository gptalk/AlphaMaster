"""Unit tests for train_cli.py pure functions (no torch / no subprocess)."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Make sure train_cli.py can be imported as a module from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import train_cli  # noqa: E402


def test_parse_args_minimal() -> None:
    args = train_cli.parse_args(["600519.SH", "H1"])
    assert args.symbol == "600519.SH"
    assert args.timeframe == "H1"
    assert args.data_dir is None
    assert args.from_scratch is False


def test_parse_args_with_data_dir() -> None:
    args = train_cli.parse_args(["XAUUSD", "H1", "--data-dir", "/tmp/kline"])
    assert args.symbol == "XAUUSD"
    assert args.timeframe == "H1"
    assert args.data_dir == "/tmp/kline"


def test_parse_args_with_from_scratch() -> None:
    args = train_cli.parse_args(["600519.SH", "H1", "--from-scratch"])
    assert args.from_scratch is True


def test_parse_args_missing_symbol_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        train_cli.parse_args(["H1"])
    assert exc_info.value.code == 2


def test_parse_args_missing_timeframe_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        train_cli.parse_args(["600519.SH"])
    assert exc_info.value.code == 2


def test_parse_args_help_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        train_cli.parse_args(["--help"])
    assert exc_info.value.code == 0


def test_resolve_data_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHAMASTER_DATA_DIR", raising=False)
    args = argparse.Namespace(data_dir=None)
    assert train_cli.resolve_data_dir(args) == train_cli.DEFAULT_DATA_DIR


def test_resolve_data_dir_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHAMASTER_DATA_DIR", "/env/data")
    args = argparse.Namespace(data_dir=None)
    assert train_cli.resolve_data_dir(args) == "/env/data"


def test_resolve_data_dir_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHAMASTER_DATA_DIR", "/env/data")
    args = argparse.Namespace(data_dir="/cli/data")
    assert train_cli.resolve_data_dir(args) == "/cli/data"


def test_build_parquet_filename_basic() -> None:
    assert train_cli.build_parquet_filename("600519.SH", "H1") == "600519.SH_H1.parquet"


def test_build_parquet_filename_no_dot() -> None:
    assert train_cli.build_parquet_filename("XAUUSD", "M5") == "XAUUSD_M5.parquet"


def test_safe_symbol_tag_replaces_dots() -> None:
    assert train_cli.safe_symbol_tag("US100.cash") == "US100_cash"


def test_safe_symbol_tag_no_op() -> None:
    assert train_cli.safe_symbol_tag("BTCUSDT") == "BTCUSDT"


def test_format_duration_hms() -> None:
    # 8132 seconds = 2h 15m 32s
    assert train_cli.format_duration(8132) == "2h 15m 32s"


def test_format_duration_seconds_only() -> None:
    assert train_cli.format_duration(45) == "0h 00m 45s"


def test_format_duration_zero() -> None:
    assert train_cli.format_duration(0) == "0h 00m 00s"


def test_format_duration_negative_returns_zero() -> None:
    assert train_cli.format_duration(-100) == "0h 00m 00s"
