"""Unit tests for analyze_cli.py (no real AI calls)."""
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

# Make sure analyze_cli.py can be imported as a module from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import analyze_cli  # noqa: E402


def test_parse_args_minimal() -> None:
    args = analyze_cli.parse_args(["600519.SH", "H1"])
    assert args.symbol == "600519.SH"
    assert args.timeframe == "H1"
    assert args.provider is None
    assert args.api_key is None
    assert args.base_url is None
    assert args.model is None


def test_parse_args_with_all_options() -> None:
    args = analyze_cli.parse_args([
        "XAUUSD", "M5",
        "--provider", "openclaw",
        "--api-key", "sk-test",
        "--base-url", "https://api.example.com",
        "--model", "gpt-4",
    ])
    assert args.symbol == "XAUUSD"
    assert args.timeframe == "M5"
    assert args.provider == "openclaw"
    assert args.api_key == "sk-test"
    assert args.base_url == "https://api.example.com"
    assert args.model == "gpt-4"


def test_parse_args_missing_symbol_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        analyze_cli.parse_args(["H1"])
    assert exc_info.value.code == 2


def test_parse_args_missing_timeframe_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        analyze_cli.parse_args(["600519.SH"])
    assert exc_info.value.code == 2


def test_parse_args_help_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        analyze_cli.parse_args(["--help"])
    assert exc_info.value.code == 0
