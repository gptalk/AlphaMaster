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
import re
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


def detect_backtest_phase(text: str) -> str:
    """Detect the latest backtest phase from accumulated log text.

    Mirrors `web.backtest_manager.BacktestManager._current_phase` logic
    (keyword-based, scans forward so the last matched phase wins).
    Returns one of: init, cost, strategy, data, compute, chart, done.
    """
    detected = "init"
    if "交易成本" in text or "手续费=" in text:
        detected = "cost"
    if "加载各品种策略" in text or "score=" in text or "模式:" in text:
        detected = "strategy"
    if "正在加载数据" in text:
        detected = "data"
    if re.search(r"品种:\s*\[", text) or "多因子回测报告" in text:
        detected = "compute"
    if "生成 K 线图" in text or "张缩放图" in text:
        detected = "chart"
    if "完成。" in text or "JSON 报告已保存" in text:
        detected = "done"
    return detected


def read_final_report(report_path: str) -> dict[str, Any] | None:
    """Read multi_factor_report.json. Returns None if missing or invalid.

    Returns a normalized dict with keys: mode, symbols (list), portfolio (dict).
    """
    path = Path(report_path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    symbols_raw = raw.get("symbols") or {}
    symbols_list = list(symbols_raw.keys()) if isinstance(symbols_raw, dict) else []
    portfolio = raw.get("portfolio") or {}
    return {
        "mode": str(raw.get("mode") or "?"),
        "symbols": symbols_list,
        "portfolio": portfolio if isinstance(portfolio, dict) else {},
    }


# ─────────────────────────────────────────────────────────────────────
# Banner printers (I/O — accept `file=...` for testability)
# ─────────────────────────────────────────────────────────────────────


def print_startup_banner(
    *,
    strategy_info: dict[str, Any],
    data_file: str,
    commission: float,
    slippage: float,
    output_dir: str,
    log_path: str,
    file=None,
) -> None:
    """Print the colored startup banner before the backtest starts."""
    if file is None:
        file = sys.stdout
    symbol = strategy_info.get("symbol", "?")
    timeframe = strategy_info.get("timeframe", "?")
    strategy_file = strategy_info.get("strategy_file") or "?"

    sep = "═" * LINE_WIDTH
    file.write(sep + "\n")
    file.write(
        f"  {ANSI_CYAN_BOLD}回测{ANSI_RESET} — {ANSI_BOLD}{Path(strategy_file).name}{ANSI_RESET} "
        f"({ANSI_BOLD}{symbol} / {timeframe}{ANSI_RESET})\n"
    )
    file.write(sep + "\n")
    file.write(f"  数据文件:  {data_file}\n")
    file.write(
        f"  交易成本:  手续费 {commission:g}% + 滑点 {slippage:g}%\n"
    )
    file.write(f"  输出目录:  {output_dir}\n")
    file.write(f"  日志文件:  {log_path}\n")
    file.write(sep + "\n\n")
    file.flush()


def print_phase_transition(
    *,
    phase_key: str,
    phase_label: str,
    file=None,
) -> None:
    """Print a phase transition line."""
    if file is None:
        file = sys.stdout
    file.write(f"{ANSI_DIM}[阶段:{phase_key}]{ANSI_RESET} {ANSI_BOLD}{phase_label}{ANSI_RESET}\n")
    file.flush()


def print_summary_banner(
    *,
    report: dict[str, Any],
    elapsed_seconds: int,
    output_dir: str,
    file=None,
) -> None:
    """Print the colored summary banner after backtest completes."""
    if file is None:
        file = sys.stdout
    sep = "─" * RULE_WIDTH
    file.write("\n" + sep + "\n")
    file.write(
        f"  {ANSI_GREEN_BOLD}✓ 回测完成{ANSI_RESET} ({elapsed_seconds} 秒)\n"
    )
    file.write(sep + "\n")

    mode = report.get("mode", "?")
    symbols = report.get("symbols") or []
    symbols_str = ", ".join(symbols) if symbols else "N/A"
    file.write(f"  模式:       {mode} ({symbols_str})\n")

    pf = report.get("portfolio") or {}

    def fmt_pct(v: Any) -> str:
        if v is None:
            return "N/A"
        try:
            return f"{float(v) * 100:+.2f}%"
        except (TypeError, ValueError):
            return "N/A"

    def fmt_num(v: Any) -> str:
        if v is None:
            return "N/A"
        try:
            return f"{float(v):.3f}"
        except (TypeError, ValueError):
            return "N/A"

    total_ret = fmt_pct(pf.get("total_return"))
    sharpe = fmt_num(pf.get("sharpe"))
    sortino = fmt_num(pf.get("sortino"))
    pl_ratio = fmt_num(pf.get("profit_loss_ratio"))

    file.write(
        f"  总收益:     {ANSI_YELLOW_BOLD}{total_ret}{ANSI_RESET}   "
        f"夏普: {ANSI_YELLOW_BOLD}{sharpe}{ANSI_RESET} "
        f"索提诺: {ANSI_YELLOW_BOLD}{sortino}{ANSI_RESET}   "
        f"盈亏比: {ANSI_YELLOW_BOLD}{pl_ratio}{ANSI_RESET}\n"
    )
    file.write(f"  资金曲线:   {output_dir}portfolio_equity.png\n")
    file.write(f"  详细报告:   {output_dir}multi_factor_report.json\n")
    file.write(sep + "\n\n")
    file.flush()


def run_backtest_subprocess(
    *,
    cmd: list[str],
    log_path: Path,
    cwd: Path,
) -> int:
    """Run `run_backtest.py` as a subprocess, tee stdout to log_path + terminal.

    Also scans each line for backtest phase keywords and emits phase
    transitions via `print_phase_transition` (only on actual transitions,
    so the user sees one line per phase, not per log line).

    Returns the subprocess returncode.

    Uses Popen + PIPE so the parent can scan log lines for phase transitions
    (mirrors the train_cli.py approach).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["LOGURU_COLORIZE"] = "0"

    # Phase labels keyed by phase id (mirrors BACKTEST_PHASES mapping).
    _PHASE_LABELS = {p[0]: p[1] for p in BACKTEST_PHASES}

    accumulated: list[str] = []
    last_phase = "init"

    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
    )
    if process.stdout is None:
        raise RuntimeError("subprocess stdout pipe is unexpectedly None")
    try:
        try:
            with log_path.open("w", encoding="utf-8", buffering=1) as log_fp:
                for line in process.stdout:
                    log_fp.write(line)
                    log_fp.flush()
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    accumulated.append(line)
                    phase = detect_backtest_phase("".join(accumulated))
                    if phase != last_phase:
                        last_phase = phase
                        print_phase_transition(
                            phase_key=phase,
                            phase_label=_PHASE_LABELS.get(phase, phase),
                        )
        finally:
            process.stdout.close()
            # Always reap the child: if anything raised mid-loop, terminate + kill.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        returncode = process.wait()
    except Exception:
        # Best-effort: ensure child is gone before propagating.
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    return int(returncode)


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """Entry point. Exits with 0 (success), 1 (backtest failed), or 2 (bad config)."""
    args = parse_args(argv)
    settings = load_settings()
    costs = merge_cost_settings(args, settings)

    # ── Validate strategy file ──
    try:
        strategy_info = inspect_strategy_file(args.strategy_file)
    except (FileNotFoundError, ValueError) as e:
        print(f"[错误] 策略文件无效: {e}", file=sys.stderr)
        print(f"        请检查 --strategy-file 参数: {args.strategy_file}", file=sys.stderr)
        sys.exit(2)

    # Strategy_info may not contain data_file — add it from args for the banner.
    strategy_info.setdefault("strategy_file", args.strategy_file)

    data_file = resolve_data_file(args, strategy_info)
    if not data_file:
        print("[错误] 无法确定数据文件路径。", file=sys.stderr)
        print("        策略 JSON 缺 data_file 字段，且 CLI 未提供 --data-file。", file=sys.stderr)
        sys.exit(2)

    # ── Build subprocess command ──
    started_at = _now_utc()
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_filename = f"backtest_{started_at.strftime('%Y%m%d_%H%M%S')}.log"
    log_path = log_dir / log_filename
    rel_log_path = str(log_path.relative_to(PROJECT_ROOT)).replace("\\", "/")

    cmd = [
        sys.executable,
        "-u",
        "run_backtest.py",
        "--strategy-file",
        args.strategy_file,
        "--data-file",
        data_file,
        "--commission",
        str(costs["commission"]),
        "--slippage",
        str(costs["slippage"]),
    ]

    # ── Startup banner ──
    print_startup_banner(
        strategy_info=strategy_info,
        data_file=data_file,
        commission=costs["commission"],
        slippage=costs["slippage"],
        output_dir="backtest_output/",
        log_path=rel_log_path,
        file=sys.stdout,
    )

    # ── Run subprocess (with phase tracking) ──
    returncode = 1
    try:
        returncode = run_backtest_subprocess(
            cmd=cmd,
            log_path=log_path,
            cwd=PROJECT_ROOT,
        )
    finally:
        finished_at = _now_utc()
        elapsed = int((finished_at - started_at).total_seconds())

    if returncode != 0:
        print(f"\n[错误] 回测子进程退出码 {returncode}", file=sys.stderr)
        print(f"        详细日志: {rel_log_path}", file=sys.stderr)
        sys.exit(1)

    # ── Read final report ──
    report = read_final_report(str(REPORT_PATH))
    if report is None:
        print(f"[错误] 回测报告未生成: {REPORT_PATH}", file=sys.stderr)
        sys.exit(1)

    # ── Summary banner ──
    print_summary_banner(
        report=report,
        elapsed_seconds=elapsed,
        output_dir="backtest_output/",
        file=sys.stdout,
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
