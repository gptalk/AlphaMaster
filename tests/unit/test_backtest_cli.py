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
    assert "compute" in out
    assert "回测计算" in out
    assert "[阶段" in out


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
    assert "[阶段" in captured.out
    # Each phase label should appear (cost → strategy → data → compute → chart → done).
    for label in ["交易成本", "加载各品种策略", "正在加载数据", "完成"]:
        assert label in captured.out, f"missing phase: {label}"


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
