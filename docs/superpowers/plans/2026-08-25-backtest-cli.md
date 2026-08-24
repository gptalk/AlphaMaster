# backtest_cli.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `backtest_cli.py` — a CLI mirror of the web "回测" module. Takes `--strategy-file` + optional cost/data args, runs `run_backtest.py` subprocess with PIPE+tee, prints phase progress + final summary banner.

**Architecture:** Single new file (`backtest_cli.py`, ~180 LOC). Reuses `web.strategy_file.inspect_strategy_file` (strategy validation), `web.backtest_manager.BACKTEST_PHASES` (phase constants + keywords), `web.settings.load_settings` (cost defaults). Spawns `run_backtest.py` as subprocess using the proven PIPE+tee pattern from `train_cli.py`.

**Tech Stack:** Python 3.11+, stdlib + project modules (`web.strategy_file`, `web.backtest_manager`, `web.settings`). **Zero new third-party deps.**

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backtest_cli.py` | Create | CLI implementation: arg parsing, settings merge, strategy validation, subprocess orchestration, banner print, phase detection, summary aggregation |
| `tests/unit/test_backtest_cli.py` | Create | Unit tests with monkeypatched `subprocess.run`, `inspect_strategy_file`, etc. |

`backtest_cli.py` decomposed into pure functions + orchestrator `main()`:
- `parse_args(argv)` — argparse
- `resolve_data_file(args, strategy_info)` — CLI > strategy.data_file > error
- `merge_cost_settings(args, settings)` — CLI > settings > defaults
- `_now_utc()` — datetime helper
- `detect_backtest_phase(text)` — pure phase detection from log content
- `read_final_report(report_path)` — pure JSON parser
- `print_startup_banner(strategy_info, data_file, commission, slippage, output_dir, log_path, file)`
- `print_phase_transition(phase_key, phase_label, file)`
- `print_summary_banner(report, elapsed_seconds, file)`
- `run_backtest_subprocess(cmd, log_path, cwd)` — Popen + PIPE + tee
- `main(argv=None)` — orchestrator

---

## Task 1: Project setup + test scaffold

**Files:**
- Create: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Create empty test file with imports**

Write `tests/unit/test_backtest_cli.py`:
```python
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
```

- [ ] **Step 2: Run pytest to confirm import will fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v
```
Expected: `ModuleNotFoundError: No module named 'backtest_cli'` — confirms test discovery works.

- [ ] **Step 3: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add tests/unit/test_backtest_cli.py && git commit -m "test(backtest_cli): scaffold test file with imports"
```

---

## Task 2: Implement `parse_args`

**Files:**
- Create: `backtest_cli.py`
- Test: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_backtest_cli.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "parse_args"
```
Expected: 4 `AttributeError` failures.

- [ ] **Step 3: Implement `parse_args` + module skeleton**

Write `backtest_cli.py`:
```python
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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sure imports work when running as `python backtest_cli.py ...` from project root.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Forward-looking imports — used by main(). Kept at module top level so tests can
# `monkeypatch.setattr("backtest_cli.inspect_strategy_file", ...)`.
from web.backtest_manager import BACKTEST_PHASES, JobState  # noqa: E402
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
        help=f"单边手续费 %（默认从 web_settings.json 读，否则 {DEFAULT_COMMISSION_PCT}）",
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=None,
        help=f"单边滑点 %（默认从 web_settings.json 读，否则 {DEFAULT_SLIPPAGE_PCT}）",
    )
    return parser.parse_args(argv)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "parse_args"
```
Expected: All 4 `test_parse_args_*` tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add backtest_cli.py tests/unit/test_backtest_cli.py && git commit -m "feat(backtest_cli): parse_args — argparse --strategy-file (required) + --data-file/--commission/--slippage"
```

---

## Task 3: Implement `resolve_data_file`, `merge_cost_settings`, `_now_utc`

**Files:**
- Modify: `backtest_cli.py`
- Test: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_backtest_cli.py`:
```python
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
    assert result == {"commission": DEFAULT_COMMISSION_PCT, "slippage": DEFAULT_SLIPPAGE_PCT}


def test_now_utc_default_returns_current_time() -> None:
    before = datetime.now(timezone.utc)
    result = backtest_cli._now_utc()
    after = datetime.now(timezone.utc)
    assert before <= result <= after
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "resolve_data_file or merge_cost_settings or now_utc"
```
Expected: 7 `AttributeError` failures.

- [ ] **Step 3: Implement the three functions**

Add to `backtest_cli.py` (after `parse_args`):
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "resolve_data_file or merge_cost_settings or now_utc"
```
Expected: All 7 new tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add backtest_cli.py tests/unit/test_backtest_cli.py && git commit -m "feat(backtest_cli): resolve_data_file + merge_cost_settings + _now_utc — 配置优先级合并"
```

---

## Task 4: Implement `detect_backtest_phase`

**Files:**
- Modify: `backtest_cli.py`
- Test: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_backtest_cli.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "detect_phase"
```
Expected: 9 `AttributeError` failures.

- [ ] **Step 3: Implement `detect_backtest_phase`**

Add to `backtest_cli.py`:
```python
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
    if "品种:" in text or "多因子回测报告" in text:
        detected = "compute"
    if "生成 K 线图" in text or "张缩放图" in text:
        detected = "chart"
    if "完成。" in text or "JSON 报告已保存" in text:
        detected = "done"
    return detected
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "detect_phase"
```
Expected: All 9 new tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add backtest_cli.py tests/unit/test_backtest_cli.py && git commit -m "feat(backtest_cli): detect_backtest_phase — 从日志文本检测当前阶段"
```

---

## Task 5: Implement `read_final_report`

**Files:**
- Modify: `backtest_cli.py`
- Test: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_backtest_cli.py`:
```python
def test_read_final_report_single(tmp_path: Path) -> None:
    """Single-symbol report: has 'portfolio' block + 'symbols' with one entry."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "mode": "single",
        "symbols": {"600519.SH": {"total_return": 1.25, "sharpe": 1.56}},
        "portfolio": {
            "total_return": 1.252,
            "sharpe": 1.562,
            "sortino": 2.226,
            "profit_loss_ratio": 3.034,
        },
    }))
    result = backtest_cli.read_final_report(str(report_path))
    assert result is not None
    assert result["mode"] == "single"
    assert result["symbols"] == ["600519.SH"]
    assert result["portfolio"]["total_return"] == 1.252


def test_read_final_report_multi(tmp_path: Path) -> None:
    """Multi-symbol report: 'symbols' dict has multiple entries."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "mode": "multi",
        "symbols": {"600519.SH": {"total_return": 1.25}, "BTCUSDT": {"total_return": 0.5}},
        "portfolio": {"total_return": 1.0, "sharpe": 1.5},
    }))
    result = backtest_cli.read_final_report(str(report_path))
    assert result is not None
    assert sorted(result["symbols"]) == ["600519.SH", "BTCUSDT"]


def test_read_final_report_missing_file(tmp_path: Path) -> None:
    result = backtest_cli.read_final_report(str(tmp_path / "nonexistent.json"))
    assert result is None


def test_read_final_report_invalid_json(tmp_path: Path) -> None:
    report_path = tmp_path / "bad.json"
    report_path.write_text("not json {{{")
    result = backtest_cli.read_final_report(str(report_path))
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "read_final_report"
```
Expected: 4 `AttributeError` failures.

- [ ] **Step 3: Implement `read_final_report`**

Add to `backtest_cli.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "read_final_report"
```
Expected: All 4 new tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add backtest_cli.py tests/unit/test_backtest_cli.py && git commit -m "feat(backtest_cli): read_final_report — 解析 multi_factor_report.json"
```

---

## Task 6: Implement banner printers

**Files:**
- Modify: `backtest_cli.py`
- Test: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_backtest_cli.py`:
```python
def test_print_startup_banner_contains_key_fields() -> None:
    strategy_info = {"symbol": "600519.SH", "timeframe": "D1"}
    buf = io.StringIO()
    backtest_cli.print_startup_banner(
        strategy_info=strategy_info,
        data_file="/tmp/data.parquet",
        commission=0.02,
        slippage=0.01,
        output_dir="backtest_output/",
        log_path="logs/backtest_x.log",
        file=buf,
    )
    out = buf.getvalue()
    assert "600519.SH" in out
    assert "D1" in out
    assert "/tmp/data.parquet" in out
    assert "0.02" in out
    assert "0.01" in out
    assert "backtest_output/" in out
    assert "logs/backtest_x.log" in out


def test_print_phase_transition_basic() -> None:
    buf = io.StringIO()
    backtest_cli.print_phase_transition(phase_key="compute", phase_label="回测计算", file=buf)
    out = buf.getvalue()
    assert "compute" in out or "回测计算" in out
    assert "[阶段]" in out


def test_print_summary_banner_success_contains_fields() -> None:
    report = {
        "mode": "single",
        "symbols": ["600519.SH"],
        "portfolio": {
            "total_return": 1.252,
            "sharpe": 1.562,
            "sortino": 2.226,
            "profit_loss_ratio": 3.034,
        },
    }
    buf = io.StringIO()
    backtest_cli.print_summary_banner(
        report=report,
        elapsed_seconds=42,
        output_dir="backtest_output/",
        file=buf,
    )
    out = buf.getvalue()
    assert "回测完成" in out
    assert "42" in out
    assert "single" in out
    assert "600519.SH" in out
    assert "1.252" in out or "125.2" in out  # 125.21% or 1.252
    assert "1.562" in out
    assert "2.226" in out
    assert "3.034" in out


def test_print_summary_banner_missing_fields_show_na() -> None:
    report = {"mode": "single", "symbols": [], "portfolio": {}}
    buf = io.StringIO()
    backtest_cli.print_summary_banner(
        report=report,
        elapsed_seconds=10,
        output_dir="backtest_output/",
        file=buf,
    )
    out = buf.getvalue()
    assert "N/A" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "banner or phase_transition"
```
Expected: 4 `AttributeError` failures.

- [ ] **Step 3: Implement the banner printers**

Add to `backtest_cli.py`:
```python
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
        f"  {ANSI_CYAN_BOLD}回测{ANSI_RESET} — {ANSI_BOLD}{Path_str(strategy_file).name}{ANSI_RESET} "
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
    file.write(f"{ANSI_DIM}[阶段]{ANSI_RESET} {ANSI_BOLD}{phase_label}{ANSI_RESET}\n")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "banner or phase_transition"
```
Expected: All 4 banner tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add backtest_cli.py tests/unit/test_backtest_cli.py && git commit -m "feat(backtest_cli): print_startup_banner + print_phase_transition + print_summary_banner — ANSI 彩色横幅"
```

---

## Task 7: Implement `run_backtest_subprocess`

**Files:**
- Modify: `backtest_cli.py`
- Test: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_backtest_cli.py`:
```python
def test_run_backtest_subprocess_returns_zero_on_success(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    rc = backtest_cli.run_backtest_subprocess(
        cmd=[sys.executable, "-c", "print('ok')"],
        log_path=log,
        cwd=PROJECT_ROOT,
    )
    assert rc == 0
    assert "ok" in log.read_text(encoding="utf-8")


def test_run_backtest_subprocess_returns_nonzero_on_failure(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    rc = backtest_cli.run_backtest_subprocess(
        cmd=[sys.executable, "-c", "import sys; sys.exit(7)"],
        log_path=log,
        cwd=PROJECT_ROOT,
    )
    assert rc == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "run_backtest_subprocess"
```
Expected: 2 `AttributeError` failures.

- [ ] **Step 3: Implement `run_backtest_subprocess`**

Add to `backtest_cli.py`:
```python
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

    # Phase labels keyed by phase id (mirrors BACKTEST_PHASES order from web).
    _PHASE_LABELS = {p[0]: p[1] for p in BACKTEST_PHASES}

    accumulated: list[str] = []
    last_phase = "init"

    with log_path.open("w", encoding="utf-8", buffering=1) as log_fp:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )
        assert process.stdout is not None
        try:
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
        returncode = process.wait()
    return int(returncode)
```

- [ ] **Step 4: Add tests for phase transition emission**

Append to `tests/unit/test_backtest_cli.py`:
```python
def test_run_backtest_subprocess_emits_phase_transitions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Phases printed as log lines accumulate; transitions emitted on changes only."""
    log = tmp_path / "out.log"
    # Simulate run_backtest.py output covering several phases.
    script = (
        "import sys\n"
        "print('交易成本: 手续费=0.02%')\n"
        "print('加载各品种策略: ...')\n"
        "print('正在加载数据')\n"
        "print('品种: [X]')\n"
        "print('生成 K 线图')\n"
        "print('完成。')\n"
    )
    rc = backtest_cli.run_backtest_subprocess(
        cmd=[sys.executable, "-c", script],
        log_path=log,
        cwd=PROJECT_ROOT,
    )
    assert rc == 0
    captured = capsys.readouterr()
    # Verify phase transitions appeared in terminal output.
    assert "[阶段]" in captured.out
    # Each phase label should appear (cost → strategy → data → compute → chart → done).
    for label in ["交易成本", "加载各品种策略", "正在加载数据", "完成"]:
        assert label in captured.out, f"missing phase: {label}"


def test_run_backtest_subprocess_returns_zero_on_success(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    rc = backtest_cli.run_backtest_subprocess(
        cmd=[sys.executable, "-c", "print('ok')"],
        log_path=log,
        cwd=PROJECT_ROOT,
    )
    assert rc == 0
    assert "ok" in log.read_text(encoding="utf-8")


def test_run_backtest_subprocess_returns_nonzero_on_failure(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    rc = backtest_cli.run_backtest_subprocess(
        cmd=[sys.executable, "-c", "import sys; sys.exit(7)"],
        log_path=log,
        cwd=PROJECT_ROOT,
    )
    assert rc == 7
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "run_backtest_subprocess"
```
Expected: All 3 tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add backtest_cli.py tests/unit/test_backtest_cli.py && git commit -m "feat(backtest_cli): run_backtest_subprocess — Popen + PIPE + tee 日志 + 阶段内联打印"
```

---

## Task 8: Implement `main()` orchestrator

**Files:**
- Modify: `backtest_cli.py`
- Test: `tests/unit/test_backtest_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_backtest_cli.py`:
```python
def test_main_missing_strategy_file_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Argparse enforces --strategy-file; missing it exits 2."""
    monkeypatch.setattr(sys, "argv", ["backtest_cli.py"])  # no --strategy-file
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.main()
    assert exc_info.value.code == 2


def test_main_strategy_file_missing_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["backtest_cli.py", "--strategy-file", "/nonexistent.json"]
    )
    monkeypatch.setattr("backtest_cli.load_settings", lambda: {})
    monkeypatch.setattr(
        "backtest_cli.inspect_strategy_file",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.main()
    assert exc_info.value.code == 2


def test_main_data_file_unresolvable_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest_cli.py", "--strategy-file", "strategies/best_X.json"],
    )
    monkeypatch.setattr("backtest_cli.load_settings", lambda: {})
    monkeypatch.setattr(
        "backtest_cli.inspect_strategy_file",
        lambda _path: {"symbol": "X", "timeframe": "H1"},  # no data_file
    )
    monkeypatch.setattr(
        "backtest_cli.resolve_data_file",
        lambda _args, _info: None,  # can't resolve
    )
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.main()
    assert exc_info.value.code == 2


def test_main_happy_path_exits_0(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest_cli.py", "--strategy-file", "strategies/best_X.json"],
    )
    monkeypatch.setattr("backtest_cli.load_settings", lambda: {})
    monkeypatch.setattr(
        "backtest_cli.inspect_strategy_file",
        lambda _path: {"symbol": "X", "timeframe": "H1", "data_file": "/x.parquet"},
    )
    monkeypatch.setattr("backtest_cli.run_backtest_subprocess", lambda **_: 0)
    monkeypatch.setattr(
        "backtest_cli.read_final_report",
        lambda _path: {
            "mode": "single", "symbols": ["X"],
            "portfolio": {
                "total_return": 1.25, "sharpe": 1.5,
                "sortino": 2.0, "profit_loss_ratio": 3.0,
            },
        },
    )
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "X" in captured.out
    assert "回测完成" in captured.out


def test_main_subprocess_fails_exits_1(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest_cli.py", "--strategy-file", "strategies/best_X.json"],
    )
    monkeypatch.setattr("backtest_cli.load_settings", lambda: {})
    monkeypatch.setattr(
        "backtest_cli.inspect_strategy_file",
        lambda _path: {"symbol": "X", "timeframe": "H1", "data_file": "/x.parquet"},
    )
    monkeypatch.setattr("backtest_cli.run_backtest_subprocess", lambda **_: 1)
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.main()
    assert exc_info.value.code == 1


def test_main_missing_report_exits_1(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest_cli.py", "--strategy-file", "strategies/best_X.json"],
    )
    monkeypatch.setattr("backtest_cli.load_settings", lambda: {})
    monkeypatch.setattr(
        "backtest_cli.inspect_strategy_file",
        lambda _path: {"symbol": "X", "timeframe": "H1", "data_file": "/x.parquet"},
    )
    monkeypatch.setattr("backtest_cli.run_backtest_subprocess", lambda **_: 0)
    monkeypatch.setattr("backtest_cli.read_final_report", lambda _path: None)
    with pytest.raises(SystemExit) as exc_info:
        backtest_cli.main()
    assert exc_info.value.code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "main"
```
Expected: 6 failures.

- [ ] **Step 3: Implement `main()`**

Add to `backtest_cli.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v -k "main"
```
Expected: All 6 `test_main_*` tests pass.

- [ ] **Step 5: Run full suite to verify no regressions**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python -m pytest tests/unit/test_backtest_cli.py -v
```
Expected: All ~33 tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git add backtest_cli.py tests/unit/test_backtest_cli.py && git commit -m "feat(backtest_cli): main() 编排器 + 错误处理 + 单元测试完整覆盖"
```

---

## Task 9: Manual smoke test

**Files:**
- (no file changes — verification only)

- [ ] **Step 1: Run with missing --strategy-file, expect argparse to exit 2**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python backtest_cli.py
```
Expected: `usage: backtest_cli.py ...` + `error: the following arguments are required: --strategy-file`. Exit 2.

- [ ] **Step 2: Run --help, expect usage banner**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python backtest_cli.py --help
```
Expected: Clean help with `--strategy-file`, `--data-file`, `--commission`, `--slippage`. Exits 0.

- [ ] **Step 3: Run with non-existent strategy file, expect exit 2**

Run:
```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && python backtest_cli.py --strategy-file /nonexistent.json
```
Expected: `[错误] 策略文件无效: ...` Exit 2.

- [ ] **Step 4: Verify importable from anywhere**

Run:
```bash
cd /tmp && python -c "import sys; sys.path.insert(0, '/home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli'); import backtest_cli; print('OK:', backtest_cli.DEFAULT_COMMISSION_PCT, backtest_cli.DEFAULT_SLIPPAGE_PCT)"
```
Expected: `OK: 0.02 0.01`

- [ ] **Step 5: Commit any final tweaks**

```bash
cd /home/yellow/mcp/AlphaMaster/.worktrees/backtest-cli && git status
```
If anything changed:
```bash
git add -A && git commit -m "chore(backtest_cli): smoke-test fixes"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** CLI args (Task 2) / merge_settings + resolve_data_file (Task 3) / detect_phase (Task 4) / read_final_report (Task 5) / banners (Task 6) / subprocess (Task 7) / main orchestrator (Task 8) / smoke test (Task 9) / error handling (Task 8) / exit codes 0/1/2.
- [x] **Placeholder scan:** No TBD/TODO. Every step has actual code.
- [x] **Type consistency:** All helpers used in `main()` are defined in Tasks 2-7. Signatures match.
- [x] **Backwards compatibility:** No existing module modified — `BACKTEST_PHASES` is only imported (read-only).

**No known limitations for v1.**