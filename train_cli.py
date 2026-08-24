"""train_cli.py — 命令行训练客户端（镜像 web 端"模型训练"模块）。

用法:
    python train_cli.py SYMBOL TIMEFRAME [--data-dir DIR] [--from-scratch]

示例:
    python train_cli.py 600519.SH H1
    python train_cli.py XAUUSD H1 --data-dir /mnt/kline --from-scratch
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sure imports work when running as `python train_cli.py ...` from project root.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Forward-looking imports — used by run_training_subprocess (Task 6) and main() (Task 7).
# Kept at module top level so tests can `monkeypatch.setattr("train_cli.inspect_parquet_file", ...)`.
from data_pipeline.parquet_manager import inspect_parquet_file
from model_core.config import ModelConfig
from web.training_time import get_training_time_summary, record_training_session


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

DEFAULT_DATA_DIR = "data/kline/"
ENV_DATA_DIR = "ALPHAMASTER_DATA_DIR"

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


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (testable)
# ─────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Returns a Namespace with: symbol, timeframe, data_dir, from_scratch.
    """
    parser = argparse.ArgumentParser(
        prog="train_cli.py",
        description=(
            "AlphaMaster 命令行训练客户端 — 镜像 web 端'模型训练'模块。"
            "传品种+周期，CLI 自动定位 parquet 文件并展示完整训练结果。"
        ),
    )
    parser.add_argument("symbol", help="股票/品种代码（例: 600519.SH / XAUUSD）")
    parser.add_argument(
        "timeframe",
        help="K线周期（例: M1/M5/M15/H1/H4/D1/W1/MN1，支持 1h/60min 等别名）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"parquet 根目录（默认: {DEFAULT_DATA_DIR}，可被环境变量 {ENV_DATA_DIR} 覆盖）",
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="删除已有 checkpoint，从头训练（透传给 train_file.py）",
    )
    return parser.parse_args(argv)


def resolve_data_dir(args: argparse.Namespace) -> str:
    """Resolve data directory with priority: --data-dir > env > default."""
    if args.data_dir:
        return args.data_dir
    return os.environ.get(ENV_DATA_DIR, DEFAULT_DATA_DIR)


def build_parquet_filename(symbol: str, timeframe: str) -> str:
    """Build parquet filename: `{symbol}_{timeframe}.parquet`."""
    return f"{symbol}_{timeframe}.parquet"


def safe_symbol_tag(symbol: str) -> str:
    """Replace dots with underscores (matches web/progress.py:_safe_symbol_tag)."""
    return symbol.replace(".", "_")


def format_duration(seconds: int | None) -> str:
    """Format seconds as 'Hh Mm Ss'. Negative or None → '0h 00m 00s'."""
    if seconds is None or seconds < 0:
        seconds = 0
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s"
