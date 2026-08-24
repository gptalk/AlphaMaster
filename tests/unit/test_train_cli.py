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
