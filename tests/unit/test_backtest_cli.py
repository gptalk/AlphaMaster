"""Unit tests for backtest_cli.py (no actual backtest runs)."""
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

# Make sure backtest_cli.py can be imported as a module from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import backtest_cli  # noqa: E402


def test_parse_args_required_only() -> None:
    args = backtest_cli.parse_args(["--strategy-file", "strategies/best_600519.SH.json"])
    assert args.strategy_file == "strategies/best_600519.SH.json"
    assert args.data_file is None
    assert args.commission is None
    assert args.slippage is None


def test_parse_args_with_all_options() -> None:
    args = backtest_cli.parse_args([
        "--strategy-file", "strategies/best_600519.SH.json",
        "--data-file", "/tmp/data.parquet",
        "--commission", "0.05",
        "--slippage", "0.02",
    ])
    assert args.strategy_file == "strategies/best_600519.SH.json"
    assert args.data_file == "/tmp/data.parquet"
    assert args.commission == 0.05
    assert args.slippage == 0.02


def test_parse_args_missing_strategy_file_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.parse_args([])
    assert exc_info.value.code == 2


def test_parse_args_help_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.parse_args(["--help"])
    assert exc_info.value.code == 0


def test_resolve_data_file_cli_wins() -> None:
    args = argparse.Namespace(data_file="/cli/data.parquet")
    strategy_info = {"data_file": "/strategy/data.parquet"}
    assert backtest_cli.resolve_data_file(args, strategy_info) == "/cli/data.parquet"


def test_resolve_data_file_falls_back_to_strategy() -> None:
    args = argparse.Namespace(data_file=None)
    strategy_info = {"data_file": "/strategy/data.parquet"}
    assert backtest_cli.resolve_data_file(args, strategy_info) == "/strategy/data.parquet"


def test_resolve_data_file_no_source_returns_none() -> None:
    args = argparse.Namespace(data_file=None)
    strategy_info = {}  # no data_file field
    assert backtest_cli.resolve_data_file(args, strategy_info) is None


def test_merge_cost_settings_cli_wins() -> None:
    args = argparse.Namespace(commission=0.05, slippage=0.03)
    settings = {"bt_commission_pct": 0.02, "bt_slippage_pct": 0.01}
    result = backtest_cli.merge_cost_settings(args, settings)
    assert result == {"commission": 0.05, "slippage": 0.03}


def test_merge_cost_settings_falls_back_to_settings() -> None:
    args = argparse.Namespace(commission=None, slippage=None)
    settings = {"bt_commission_pct": 0.03, "bt_slippage_pct": 0.02}
    result = backtest_cli.merge_cost_settings(args, settings)
    assert result == {"commission": 0.03, "slippage": 0.02}


def test_merge_cost_settings_falls_back_to_defaults() -> None:
    args = argparse.Namespace(commission=None, slippage=None)
    settings = {}  # no cost fields
    result = backtest_cli.merge_cost_settings(args, settings)
    assert result == {"commission": backtest_cli.DEFAULT_COMMISSION_PCT, "slippage": backtest_cli.DEFAULT_SLIPPAGE_PCT}


def test_now_utc_default_returns_current_time() -> None:
    before = datetime.now(timezone.utc)
    result = backtest_cli._now_utc()
    after = datetime.now(timezone.utc)
    assert before <= result <= after


def test_detect_phase_empty_text() -> None:
    assert backtest_cli.detect_backtest_phase("") == "init"


def test_detect_phase_init_default() -> None:
    """Text with no keywords → init."""
    assert backtest_cli.detect_backtest_phase("some random text\nmore text") == "init"


def test_detect_phase_cost() -> None:
    text = "交易成本（单边）: 手续费=0.02% 滑点=0.01%"
    assert backtest_cli.detect_backtest_phase(text) == "cost"


def test_detect_phase_strategy() -> None:
    text = "加载各品种策略 score=10.245 模式: ftmo"
    assert backtest_cli.detect_backtest_phase(text) == "strategy"


def test_detect_phase_data() -> None:
    text = "正在加载数据 600519.SH_1d.parquet"
    assert backtest_cli.detect_backtest_phase(text) == "data"


def test_detect_phase_compute() -> None:
    text = "品种: ['600519.SH'] 多因子回测报告"
    assert backtest_cli.detect_backtest_phase(text) == "compute"


def test_detect_phase_chart() -> None:
    text = "生成 K 线图 5 张缩放图"
    assert backtest_cli.detect_backtest_phase(text) == "chart"


def test_detect_phase_done() -> None:
    text = "完成。 JSON 报告已保存"
    assert backtest_cli.detect_backtest_phase(text) == "done"


def test_detect_phase_picks_latest() -> None:
    """With multiple keywords, the last matched phase wins (scanning forward)."""
    text = (
        "交易成本: ...\n"
        "加载各品种策略: ...\n"
        "正在加载数据: ...\n"
        "品种: [...]\n"
        "完成。\n"
    )
    assert backtest_cli.detect_backtest_phase(text) == "done"


def test_detect_phase_compute_requires_brackets() -> None:
    """'品种:' alone (without '[') should NOT trigger compute phase."""
    # Mirrors web/backtest_manager.py regex: r"品种:\s*\["
    assert backtest_cli.detect_backtest_phase("品种: 数据为空") != "compute"
    # But with brackets (real report output) it should match
    assert backtest_cli.detect_backtest_phase("品种: ['600519.SH']") == "compute"
