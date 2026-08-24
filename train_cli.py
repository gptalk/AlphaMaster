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