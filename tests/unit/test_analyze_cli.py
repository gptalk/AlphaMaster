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


def test_merge_settings_cli_wins_over_settings() -> None:
    args = argparse.Namespace(
        provider="openclaw",
        api_key="cli-key",
        base_url="https://cli.example.com",
        model="cli-model",
    )
    settings = {
        "ai_provider": "deepseek",
        "ai_api_key": "settings-key",
        "ai_base_url": "https://settings.com",
        "ai_model": "settings-model",
    }
    result = analyze_cli._merge_settings(args, settings)
    assert result == {
        "provider": "openclaw",
        "api_key": "cli-key",
        "base_url": "https://cli.example.com",
        "model": "cli-model",
    }


def test_merge_settings_falls_back_to_settings() -> None:
    args = argparse.Namespace(provider=None, api_key=None, base_url=None, model=None)
    settings = {
        "ai_provider": "openclaw_wb",
        "ai_api_key": "settings-key",
        "ai_base_url": "https://settings.com",
        "ai_model": "settings-model",
    }
    result = analyze_cli._merge_settings(args, settings)
    assert result == {
        "provider": "openclaw_wb",
        "api_key": "settings-key",
        "base_url": "https://settings.com",
        "model": "settings-model",
    }


def test_merge_settings_falls_back_to_defaults() -> None:
    args = argparse.Namespace(provider=None, api_key=None, base_url=None, model=None)
    settings = {}  # empty settings (no AI keys)
    result = analyze_cli._merge_settings(args, settings)
    assert result == {
        "provider": "deepseek",
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    }


def test_build_cli_snapshot_basic_shape() -> None:
    """build_cli_snapshot should produce the same shape as web/ai_analyze.build_training_snapshot."""
    fake_snapshot = {
        "symbol": "FAKE",
        "timeframe": "H1",
        "data_file": None,
        "training_active": False,
        "job_state": None,
        "current_step": 1000,
        "train_steps": 5000,
        "progress_pct": 20.0,
        "status": "in_progress",
        "best_score": 5.5,
        "strategy_score": 5.5,
        "has_strategy": False,
        "formula": [1, 2, 3],
        "formula_decoded": "alpha → close",
        "checkpoint_path": None,
        "training_curve": {"total_points": 0, "sampled": False, "points": 0, "series": {}},
        "history_summary": {},
    }
    # monkeypatch the underlying web builder
    with patch("analyze_cli.build_training_snapshot", return_value=fake_snapshot) as mock:
        result = analyze_cli.build_cli_snapshot("FAKE", "H1")
    assert result is fake_snapshot
    mock.assert_called_once_with("FAKE", "H1")
