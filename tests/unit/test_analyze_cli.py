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


def test_print_snapshot_banner_contains_key_fields() -> None:
    snapshot = {
        "symbol": "600519.SH",
        "timeframe": "H1",
        "current_step": 4500,
        "train_steps": 9000,
        "best_score": 10.245,
        "strategy_score": 10.245,
        "val_score": 2.83,  # may or may not appear depending on impl
        "formula_decoded": "alpha → close → ts_mean(5)",
        "progress_pct": 50.0,
    }
    buf = io.StringIO()
    analyze_cli.print_snapshot_banner(
        snapshot=snapshot,
        prior_count=2,
        provider="deepseek",
        model="deepseek-v4-flash",
        file=buf,
    )
    out = buf.getvalue()
    assert "600519.SH" in out
    assert "H1" in out
    assert "4,500" in out  # current_step formatted
    assert "9,000" in out  # train_steps formatted
    assert "10.245" in out
    assert "alpha → close → ts_mean(5)" in out
    assert "deepseek" in out
    assert "deepseek-v4-flash" in out
    assert "2 次" in out  # prior_count


def test_print_snapshot_banner_handles_none_progress() -> None:
    snapshot = {
        "symbol": "X",
        "timeframe": "H1",
        "current_step": None,
        "train_steps": None,
        "best_score": None,
        "formula_decoded": None,
        "progress_pct": None,
    }
    buf = io.StringIO()
    analyze_cli.print_snapshot_banner(
        snapshot=snapshot,
        prior_count=0,
        provider="openclaw",
        model="claude",
        file=buf,
    )
    out = buf.getvalue()
    assert "N/A" in out


def test_print_summary_banner_success_contains_fields() -> None:
    meta = {"provider": "deepseek", "model": "deepseek-v4-flash"}
    buf = io.StringIO()
    analyze_cli.print_summary_banner(
        meta=meta,
        elapsed_seconds=28,
        file=buf,
    )
    out = buf.getvalue()
    assert "分析完成" in out
    assert "deepseek-v4-flash" in out
    assert "28" in out  # seconds


def test_stream_ai_answer_writes_deltas_immediately(capsys: pytest.CaptureFixture) -> None:
    """Each delta event should be written + flushed to stdout."""
    events = iter([
        {"type": "meta", "provider": "p", "model": "m"},
        {"type": "delta", "text": "Hello, "},
        {"type": "delta", "text": "world!"},
        {"type": "done", "provider": "p", "model": "m"},
    ])
    answer = analyze_cli.stream_ai_answer(events)
    assert answer == "Hello, world!"
    captured = capsys.readouterr()
    assert "Hello, " in captured.out
    assert "world!" in captured.out


def test_stream_ai_answer_handles_error_event() -> None:
    """An error event should raise RuntimeError with the error message."""
    events = iter([
        {"type": "meta", "provider": "p", "model": "m"},
        {"type": "error", "message": "rate limited"},
    ])
    with pytest.raises(RuntimeError, match="rate limited"):
        analyze_cli.stream_ai_answer(events)


def test_stream_ai_answer_ignores_unknown_event_types() -> None:
    events = iter([
        {"type": "meta", "provider": "p", "model": "m"},
        {"type": "weird_event", "data": "ignored"},
        {"type": "delta", "text": "ok"},
        {"type": "done", "provider": "p", "model": "m"},
    ])
    answer = analyze_cli.stream_ai_answer(events)
    assert answer == "ok"


def test_now_utc_default_returns_current_time() -> None:
    before = datetime.now(timezone.utc)
    result = analyze_cli._now_utc()
    after = datetime.now(timezone.utc)
    assert before <= result <= after
