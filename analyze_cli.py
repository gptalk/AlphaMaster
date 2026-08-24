"""analyze_cli.py — 命令行 AI 训练结果分析（镜像 web 端"AI 分析"模块）。

用法:
    python analyze_cli.py SYMBOL TIMEFRAME [--provider P] [--api-key K] [--base-url U] [--model M]

示例:
    python analyze_cli.py 600519.SH H1
    python analyze_cli.py XAUUSD M5 --provider openclaw --model claude-3.5-sonnet
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sure imports work when running as `python analyze_cli.py ...` from project root.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Forward-looking imports — used by main(). Kept at module top level so tests can
# `monkeypatch.setattr("analyze_cli.analyze_training_stream", ...)`.
from web.ai_analyze import analyze_training_stream, build_training_snapshot  # noqa: E402
from web.ai_providers import resolve_provider  # noqa: E402
from web.settings import load_settings  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

DEFAULT_PROVIDER = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

# ANSI color codes (manual — no third-party deps)
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"
ANSI_CYAN_BOLD = "\033[1;36m"
ANSI_GREEN_BOLD = "\033[1;32m"
ANSI_RED_BOLD = "\033[1;31m"
ANSI_YELLOW_BOLD = "\033[1;33m"

LINE_WIDTH = 54
RULE_WIDTH = 54


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (testable)
# ─────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments. Returns a Namespace with: symbol, timeframe, provider, api_key, base_url, model."""
    parser = argparse.ArgumentParser(
        prog="analyze_cli.py",
        description=(
            "AlphaMaster 命令行 AI 训练结果分析 — 镜像 web 端'AI 分析'模块。"
            "传品种+周期，CLI 构造训练快照并流式打印 AI 分析结果。"
        ),
    )
    parser.add_argument("symbol", help="股票/品种代码（例: 600519.SH / XAUUSD）")
    parser.add_argument(
        "timeframe",
        help="K线周期（例: M1/M5/M15/H1/H4/D1/W1/MN1）",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=f"AI provider（默认从 web_settings.json 读，否则 {DEFAULT_PROVIDER}）",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="AI provider API key（默认从 web_settings.json 读）",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"AI provider base URL（默认从 web_settings.json 读，否则 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"AI model name（默认从 web_settings.json 读，否则 {DEFAULT_MODEL}）",
    )
    return parser.parse_args(argv)
