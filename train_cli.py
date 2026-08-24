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


class _TeeWriter:
    """File-like object that writes to two underlying streams.

    Used to tee subprocess stdout to both the terminal (so tqdm is visible)
    and a log file (so users can `tail -f` or review after the fact).
    """

    def __init__(self, primary, secondary) -> None:
        self._primary = primary
        self._stream = secondary

    def write(self, data: str) -> int:
        n1 = self._primary.write(data)
        n2 = self._stream.write(data)
        return max(n1, n2)

    def flush(self) -> None:
        self._primary.flush()
        if hasattr(self._stream, "flush"):
            self._stream.flush()

    def fileno(self) -> int:
        # subprocess.Popen may call fileno() to set O_NONBLOCK; delegate.
        return self._primary.fileno()

    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    def close(self) -> None:
        # Don't close — the owner closes the underlying file/stream.
        pass


def print_startup_banner(
    *,
    symbol: str,
    info: dict[str, Any],
    target_steps: int,
    from_scratch: bool,
    file=sys.stdout,
) -> None:
    """Print the colored startup banner before training starts."""
    bars = info.get("bars", 0)
    years = info.get("years_h1")
    timeframe = info.get("timeframe", "?")
    data_file = info.get("data_file", "?")

    mode_label = "重新训练（从头）" if from_scratch else "自动续训"

    sep = "═" * LINE_WIDTH
    file.write(sep + "\n")
    file.write(f"  {ANSI_CYAN_BOLD}AlphaMaster 训练{ANSI_RESET} — {ANSI_BOLD}{symbol} / {timeframe}{ANSI_RESET}\n")
    file.write(sep + "\n")
    file.write(f"  数据文件:  {data_file}\n")
    file.write(f"  K线数量:   {ANSI_BOLD}{bars:,}{ANSI_RESET}根\n")
    if years is None:
        file.write(f"  数据年限:  {ANSI_DIM}—{ANSI_RESET}\n")
    else:
        file.write(f"  数据年限:  {ANSI_BOLD}{years}{ANSI_RESET} 年\n")
    file.write(f"  目标步数:  {target_steps:,}\n")
    file.write(f"  模式:      {mode_label}\n")
    file.write(sep + "\n\n")
    file.flush()


def print_summary_banner(
    *,
    symbol: str,
    success: bool,
    session_seconds: int | None,
    history_total_seconds: int,
    history_session_count: int,
    current_step: int | None,
    train_steps: int | None,
    best_score: float | None,
    val_score: float | None,
    formula_decoded: str | None,
    returncode: int,
    log_path: str | None,
    file=sys.stdout,
) -> None:
    """Print the colored summary banner after training completes (or fails)."""
    sep = "═" * LINE_WIDTH
    file.write("\n" + sep + "\n")
    if success:
        file.write(
            f"  {ANSI_GREEN_BOLD}✓ 训练完成{ANSI_RESET} — {ANSI_BOLD}{symbol}{ANSI_RESET}\n"
        )
    else:
        file.write(
            f"  {ANSI_RED_BOLD}✗ 训练失败{ANSI_RESET} — {ANSI_BOLD}{symbol}{ANSI_RESET}\n"
        )
    file.write(sep + "\n")
    file.write(f"  本次时长:    {format_duration(session_seconds)}\n")

    if success:
        file.write(
            f"  历史累计:    {format_duration(history_total_seconds)}  "
            f"({ANSI_DIM}{history_session_count} 次会话{ANSI_RESET})\n"
        )

        if current_step is not None and train_steps:
            pct = 100.0 * current_step / train_steps if train_steps > 0 else 0.0
            file.write(
                f"  最终进度:    {ANSI_BOLD}{current_step:,} / {train_steps:,}{ANSI_RESET} "
                f"({ANSI_GREEN_BOLD}{pct:.1f}%{ANSI_RESET})\n"
            )
        else:
            file.write(f"  最终进度:    N/A\n")

        if best_score is not None:
            file.write(f"  最优分数:    {ANSI_YELLOW_BOLD}{best_score:.4f}{ANSI_RESET}\n")
        else:
            file.write(f"  最优分数:    N/A\n")

        if val_score is not None:
            file.write(f"  验证分数:    {ANSI_YELLOW_BOLD}{val_score:.4f}{ANSI_RESET}\n")
        else:
            file.write(f"  验证分数:    N/A\n")

        if formula_decoded:
            file.write(f"  最新公式:    {ANSI_CYAN}{formula_decoded}{ANSI_RESET}\n")
        else:
            file.write(f"  最新公式:    N/A\n")

    if not success:
        file.write(f"  子进程退出码: {returncode}\n")
        if log_path:
            file.write(f"  详细日志:    {log_path}\n")

    file.write(sep + "\n\n")
    file.flush()


def run_training_subprocess(
    *,
    cmd: list[str],
    log_path: Path,
    cwd: Path,
) -> int:
    """Run `train_file.py` as a subprocess, tee stdout to log_path + terminal.

    Returns the subprocess returncode.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["LOGURU_COLORIZE"] = "0"

    with log_path.open("w", encoding="utf-8", buffering=1) as log_fp:
        tee = _TeeWriter(log_fp, sys.stdout)
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=tee,
            stderr=subprocess.STDOUT,
        )
    return int(result.returncode)
