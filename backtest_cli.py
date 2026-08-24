"""backtest_cli.py — 命令行回测客户端（镜像 web 端"回测"模块）。

用法:
    python backtest_cli.py --strategy-file PATH [--data-file PATH] [--commission C] [--slippage S]

示例:
    python backtest_cli.py --strategy-file strategies/best_600519.SH.json
    python backtest_cli.py --strategy-file strategies/best_600519.SH.json --commission 0.05
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sure imports work when running as `python backtest_cli.py ...` from project root.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Forward-looking imports — used by main(). Kept at module top level so tests can
# `monkeypatch.setattr("backtest_cli.inspect_strategy_file", ...)`.
from web.backtest_manager import BACKTEST_PHASES  # noqa: E402
from web.settings import load_settings  # noqa: E402
from web.strategy_file import inspect_strategy_file  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

DEFAULT_COMMISSION_PCT = 0.02
DEFAULT_SLIPPAGE_PCT = 0.01
OUTPUT_DIR = PROJECT_ROOT / "backtest_output"
REPORT_PATH = OUTPUT_DIR / "multi_factor_report.json"

# ANSI color codes (manual — no third-party deps)
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"
ANSI_CYAN_BOLD = "\033[1;36m"
ANSI_GREEN_BOLD = "\033[1;32m"
ANSI_RED_BOLD = "\033[1;31m"
ANSI_YELLOW_BOLD = "\033[1;33m"
ANSI_CYAN = "\033[36m"

LINE_WIDTH = 54
RULE_WIDTH = 54


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (testable)
# ─────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments. Returns a Namespace with: strategy_file, data_file, commission, slippage."""
    parser = argparse.ArgumentParser(
        prog="backtest_cli.py",
        description=(
            "AlphaMaster 命令行回测客户端 — 镜像 web 端'回测'模块。"
            "传策略 JSON，自动启动 run_backtest.py 并打印阶段进度 + 汇总指标。"
        ),
    )
    parser.add_argument(
        "--strategy-file",
        required=True,
        help="策略 JSON 路径（如 strategies/best_600519.SH.json）",
    )
    parser.add_argument(
        "--data-file",
        default=None,
        help="Parquet 数据文件路径（默认从策略 JSON 的 data_file 字段读取）",
    )
    parser.add_argument(
        "--commission",
        type=float,
        default=None,
        help=f"单边手续费 %%（默认从 web_settings.json 读，否则 {DEFAULT_COMMISSION_PCT}）",
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=None,
        help=f"单边滑点 %%（默认从 web_settings.json 读，否则 {DEFAULT_SLIPPAGE_PCT}）",
    )
    return parser.parse_args(argv)


def resolve_data_file(args: argparse.Namespace, strategy_info: dict[str, Any]) -> str | None:
    """Resolve data file: CLI > strategy.data_file > None."""
    if args.data_file:
        return args.data_file
    return strategy_info.get("data_file")


def merge_cost_settings(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, float]:
    """Resolve cost: CLI > settings > defaults."""
    return {
        "commission": (
            args.commission
            if args.commission is not None
            else settings.get("bt_commission_pct", DEFAULT_COMMISSION_PCT)
        ),
        "slippage": (
            args.slippage
            if args.slippage is not None
            else settings.get("bt_slippage_pct", DEFAULT_SLIPPAGE_PCT)
        ),
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
