"""analyze_cli.py — 命令行 AI 训练结果分析（镜像 web 端"AI 分析"模块）。

用法:
    python analyze_cli.py SYMBOL TIMEFRAME [--provider P] [--api-key K] [--base-url U] [--model M]

示例:
    python analyze_cli.py 600519.SH H1
    python analyze_cli.py XAUUSD M5 --provider openclaw --model claude-3.5-sonnet
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Make sure imports work when running as `python analyze_cli.py ...` from project root.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Forward-looking imports — used by main(). Kept at module top level so tests can
# `monkeypatch.setattr("analyze_cli.analyze_training_stream", ...)`.
from web.ai_analyze import analyze_training_stream, build_training_snapshot, load_prior_analyses  # noqa: E402
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
ANSI_CYAN = "\033[36m"

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


def _merge_settings(args: argparse.Namespace, settings: dict[str, Any]) -> dict[str, str]:
    """Merge CLI args with web_settings.json: CLI > settings > defaults."""
    return {
        "provider": args.provider or settings.get("ai_provider", "") or DEFAULT_PROVIDER,
        "api_key": args.api_key or settings.get("ai_api_key", "") or "",
        "base_url": args.base_url or settings.get("ai_base_url", "") or DEFAULT_BASE_URL,
        "model": args.model or settings.get("ai_model", "") or DEFAULT_MODEL,
    }


def build_cli_snapshot(symbol: str, timeframe: str) -> dict[str, Any]:
    """Build a training snapshot for the given (symbol, timeframe). Thin wrapper over web's build_training_snapshot."""
    return build_training_snapshot(symbol, timeframe)


def print_snapshot_banner(
    *,
    snapshot: dict[str, Any],
    prior_count: int,
    provider: str,
    model: str,
    file=sys.stdout,
) -> None:
    """Print the colored snapshot banner showing what the AI will see."""
    symbol = snapshot.get("symbol", "?")
    timeframe = snapshot.get("timeframe", "?")
    current_step = snapshot.get("current_step")
    train_steps = snapshot.get("train_steps")
    best_score = snapshot.get("best_score")
    formula_decoded = snapshot.get("formula_decoded")

    sep = "═" * LINE_WIDTH
    file.write(sep + "\n")
    file.write(f"  {ANSI_CYAN_BOLD}AI 分析{ANSI_RESET} — {ANSI_BOLD}{symbol} {timeframe}{ANSI_RESET}\n")
    file.write(sep + "\n")

    if current_step is not None and train_steps and snapshot.get("progress_pct") is not None:
        file.write(f"  训练进度:  {ANSI_BOLD}{current_step:,} /{train_steps:,}{ANSI_RESET} ({snapshot.get('progress_pct', 0):.1f}%)\n")
    else:
        file.write(f"  训练进度:  {ANSI_DIM}N/A{ANSI_RESET}\n")

    if best_score is not None:
        file.write(f"  最优分数:  {ANSI_YELLOW_BOLD}{best_score:.3f}{ANSI_RESET}\n")
    else:
        file.write(f"  最优分数:  {ANSI_DIM}N/A{ANSI_RESET}\n")

    if formula_decoded:
        file.write(f"  最新公式:  {ANSI_CYAN}{formula_decoded}{ANSI_RESET}\n")
    else:
        file.write(f"  最新公式:  {ANSI_DIM}N/A{ANSI_RESET}\n")

    file.write(f"  历史分析:  {prior_count} 次（同品种同周期）\n")
    file.write(f"  Provider:  {provider} · {model}\n")
    file.write(sep + "\n\n")
    file.flush()


def print_summary_banner(
    *,
    meta: dict[str, Any],
    elapsed_seconds: int,
    file=sys.stdout,
) -> None:
    """Print the summary banner after AI analysis completes."""
    model = meta.get("model", "?")
    sep = "─" * RULE_WIDTH
    file.write("\n" + sep + "\n")
    file.write(
        f"  {ANSI_GREEN_BOLD}✓ 分析完成{ANSI_RESET} ({model} · {elapsed_seconds} 秒)\n"
    )
    file.write(sep + "\n\n")
    file.flush()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stream_ai_answer(events: Iterator[dict[str, Any]], file=None) -> str:
    """Consume analyze_training_stream events; print deltas to file; return full answer.

    Raises RuntimeError on any 'error' event or on 'done' with empty answer.

    Note: default of None (resolved to sys.stdout at call time) so that pytest's
    capsys/capfd fixtures can intercept writes — capturing `sys.stdout` directly
    as the default would bind it at module-load time, before pytest patches stdout.
    """
    if file is None:
        file = sys.stdout
    parts: list[str] = []
    for event in events:
        etype = event.get("type")
        if etype == "meta":
            continue
        if etype == "delta":
            text = event.get("text") or ""
            parts.append(text)
            file.write(text)
            file.flush()
        elif etype == "error":
            msg = event.get("message") or "分析失败"
            raise RuntimeError(msg)
        elif etype == "done":
            answer = event.get("answer") or "".join(parts)
            if not answer.strip():
                raise RuntimeError("AI 返回内容为空")
            return answer
    raise RuntimeError("AI 流式分析未正常结束")


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """Entry point. Exits with 0 (success), 1 (AI call failed), or 2 (bad config)."""
    args = parse_args(argv)
    settings = load_settings()
    cfg = _merge_settings(args, settings)

    if not cfg["api_key"]:
        print("[错误] 缺少 API key。", file=sys.stderr)
        print("        在 web_settings.json 的 ai_api_key 字段配置，或加 --api-key 参数。", file=sys.stderr)
        sys.exit(2)

    # ── Build snapshot ──
    try:
        snapshot = build_cli_snapshot(args.symbol, args.timeframe)
    except (ValueError, FileNotFoundError) as e:
        print(f"[错误] 构造训练快照失败: {e}", file=sys.stderr)
        print("        请确认已运行过 python train_cli.py SYMBOL TIMEFRAME 完成训练。", file=sys.stderr)
        sys.exit(2)

    # ── Prior count from history ──
    prior_count = len(load_prior_analyses(args.symbol, args.timeframe))

    # ── Snapshot banner ──
    print_snapshot_banner(
        snapshot=snapshot,
        prior_count=prior_count,
        provider=cfg["provider"],
        model=cfg["model"],
        file=sys.stdout,
    )

    # ── Stream AI answer ──
    print("\n[AI 分析中...]\n", flush=True)
    started_at = _now_utc()

    try:
        events = analyze_training_stream(
            provider=cfg["provider"],
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            model=cfg["model"],
            symbol=args.symbol,
        )
        answer = stream_ai_answer(events, file=sys.stdout)
    except RuntimeError as e:
        print(f"\n[错误] AI 分析失败: {e}", file=sys.stderr)
        sys.exit(1)

    finished_at = _now_utc()
    elapsed = int((finished_at - started_at).total_seconds())

    # ── Summary banner ──
    print_summary_banner(
        meta={"provider": cfg["provider"], "model": cfg["model"]},
        elapsed_seconds=elapsed,
        file=sys.stdout,
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
