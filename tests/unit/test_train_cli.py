"""Unit tests for train_cli.py pure functions (no torch / no subprocess)."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Make sure train_cli.py can be imported as a module from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import train_cli  # noqa: E402


def test_parse_args_minimal() -> None:
    args = train_cli.parse_args(["600519.SH", "H1"])
    assert args.symbol == "600519.SH"
    assert args.timeframe == "H1"
    assert args.data_dir is None
    assert args.from_scratch is False


def test_parse_args_with_data_dir() -> None:
    args = train_cli.parse_args(["XAUUSD", "H1", "--data-dir", "/tmp/kline"])
    assert args.symbol == "XAUUSD"
    assert args.timeframe == "H1"
    assert args.data_dir == "/tmp/kline"


def test_parse_args_with_from_scratch() -> None:
    args = train_cli.parse_args(["600519.SH", "H1", "--from-scratch"])
    assert args.from_scratch is True


def test_parse_args_missing_symbol_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        train_cli.parse_args(["H1"])
    assert exc_info.value.code == 2


def test_parse_args_missing_timeframe_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        train_cli.parse_args(["600519.SH"])
    assert exc_info.value.code == 2


def test_parse_args_help_exits() -> None:
    with pytest.raises(SystemExit) as exc_info:
        train_cli.parse_args(["--help"])
    assert exc_info.value.code == 0


def test_resolve_data_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHAMASTER_DATA_DIR", raising=False)
    args = argparse.Namespace(data_dir=None)
    assert train_cli.resolve_data_dir(args) == train_cli.DEFAULT_DATA_DIR


def test_resolve_data_dir_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHAMASTER_DATA_DIR", "/env/data")
    args = argparse.Namespace(data_dir=None)
    assert train_cli.resolve_data_dir(args) == "/env/data"


def test_resolve_data_dir_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHAMASTER_DATA_DIR", "/env/data")
    args = argparse.Namespace(data_dir="/cli/data")
    assert train_cli.resolve_data_dir(args) == "/cli/data"


def test_build_parquet_filename_basic() -> None:
    assert train_cli.build_parquet_filename("600519.SH", "H1") == "600519.SH_H1.parquet"


def test_build_parquet_filename_no_dot() -> None:
    assert train_cli.build_parquet_filename("XAUUSD", "M5") == "XAUUSD_M5.parquet"


def test_safe_symbol_tag_replaces_dots() -> None:
    assert train_cli.safe_symbol_tag("US100.cash") == "US100_cash"


def test_safe_symbol_tag_no_op() -> None:
    assert train_cli.safe_symbol_tag("BTCUSDT") == "BTCUSDT"


def test_format_duration_hms() -> None:
    # 8132 seconds = 2h 15m 32s
    assert train_cli.format_duration(8132) == "2h 15m 32s"


def test_format_duration_seconds_only() -> None:
    assert train_cli.format_duration(45) == "0h 00m 45s"


def test_format_duration_zero() -> None:
    assert train_cli.format_duration(0) == "0h 00m 00s"


def test_format_duration_negative_returns_zero() -> None:
    assert train_cli.format_duration(-100) == "0h 00m 00s"


def test_format_duration_none_returns_zero() -> None:
    assert train_cli.format_duration(None) == "0h 00m 00s"


def test_tee_writer_writes_to_both(tmp_path: Path) -> None:
    log_file = tmp_path / "out.log"
    with log_file.open("w", encoding="utf-8") as fp:
        tee = train_cli._TeeWriter(fp, io.StringIO())
        tee.write("hello\n")
        tee.write("world")
        tee.flush()

    assert log_file.read_text(encoding="utf-8") == "hello\nworld"
    assert tee._stream.getvalue() == "hello\nworld"


def test_tee_writer_fileno_delegates_to_primary(tmp_path: Path) -> None:
    log_file = (tmp_path / "out.log").open("w", encoding="utf-8")
    try:
        tee = train_cli._TeeWriter(log_file, io.StringIO())
        assert tee.fileno() == log_file.fileno()
    finally:
        log_file.close()


def test_print_startup_banner_contains_key_fields() -> None:
    info = {
        "data_file": "/tmp/600519.SH_H1.parquet",
        "bars": 11520,
        "years_h1": 1.85,
        "timeframe": "H1",
    }
    buf = io.StringIO()
    train_cli.print_startup_banner(
        symbol="600519.SH",
        info=info,
        target_steps=5000,
        from_scratch=False,
        file=buf,
    )
    out = buf.getvalue()
    assert "600519.SH" in out
    assert "H1" in out
    assert "11,520" in out  # bars with thousands separator
    assert "1.85" in out
    assert "5,000" in out  # target_steps with thousands separator
    assert "自动续训" in out


def test_print_startup_banner_handles_none_years() -> None:
    info = {
        "data_file": "/tmp/X_H1.parquet",
        "bars": 100,
        "years_h1": None,
        "timeframe": "H1",
    }
    buf = io.StringIO()
    train_cli.print_startup_banner(
        symbol="X", info=info, target_steps=5000, from_scratch=False, file=buf
    )
    out = buf.getvalue()
    assert "—" in out  # years fallback
    assert "100" in out


def test_print_startup_banner_from_scratch_label() -> None:
    info = {"data_file": "/x.parquet", "bars": 100, "years_h1": 1.0, "timeframe": "H1"}
    buf = io.StringIO()
    train_cli.print_startup_banner(
        symbol="X", info=info, target_steps=100, from_scratch=True, file=buf
    )
    assert "重新训练" in buf.getvalue()


def test_print_summary_banner_success_contains_fields() -> None:
    buf = io.StringIO()
    train_cli.print_summary_banner(
        symbol="600519.SH",
        success=True,
        session_seconds=8132,
        history_total_seconds=52928,
        history_session_count=8,
        current_step=5000,
        train_steps=5000,
        best_score=2.4102,
        val_score=1.8731,
        formula_decoded="alpha → close → ts_mean(5)",
        returncode=0,
        log_path=None,
        file=buf,
    )
    out = buf.getvalue()
    assert "训练完成" in out
    assert "2h 15m 32s" in out  # session
    assert "14h 42m 08s" in out  # history
    assert "8" in out  # session count
    assert "100.0%" in out  # progress pct
    assert "2.4102" in out
    assert "1.8731" in out
    assert "alpha → close → ts_mean(5)" in out


def test_print_summary_banner_success_missing_fields_show_na() -> None:
    buf = io.StringIO()
    train_cli.print_summary_banner(
        symbol="X",
        success=True,
        session_seconds=10,
        history_total_seconds=0,
        history_session_count=0,
        current_step=100,
        train_steps=100,
        best_score=None,
        val_score=None,
        formula_decoded=None,
        returncode=0,
        log_path=None,
        file=buf,
    )
    out = buf.getvalue()
    assert "N/A" in out
    assert "训练完成" in out


def test_print_summary_banner_failure() -> None:
    buf = io.StringIO()
    train_cli.print_summary_banner(
        symbol="X",
        success=False,
        session_seconds=120,
        history_total_seconds=0,
        history_session_count=0,
        current_step=None,
        train_steps=None,
        best_score=None,
        val_score=None,
        formula_decoded=None,
        returncode=1,
        log_path="logs/train_X_20260824_120000.log",
        file=buf,
    )
    out = buf.getvalue()
    assert "训练失败" in out
    assert "1" in out  # returncode shown
    assert "logs/train_X_20260824_120000.log" in out


def test_now_utc_default_returns_current_time() -> None:
    """_now_utc returns a datetime close to now (sanity check on the seam)."""
    before = datetime.now(timezone.utc)
    result = train_cli._now_utc()
    after = datetime.now(timezone.utc)
    assert before <= result <= after


def test_run_training_subprocess_returns_zero_on_success(tmp_path: Path) -> None:
    """A trivial script that exits 0 should return 0."""
    log_path = tmp_path / "out.log"
    rc = train_cli.run_training_subprocess(
        cmd=[sys.executable, "-c", "print('ok')"],
        log_path=log_path,
        cwd=PROJECT_ROOT,
    )
    assert rc == 0
    assert "ok" in log_path.read_text(encoding="utf-8")


def test_run_training_subprocess_returns_nonzero_on_failure(tmp_path: Path) -> None:
    log_path = tmp_path / "out.log"
    rc = train_cli.run_training_subprocess(
        cmd=[sys.executable, "-c", "import sys; sys.exit(7)"],
        log_path=log_path,
        cwd=PROJECT_ROOT,
    )
    assert rc == 7


def test_main_missing_data_file_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Non-existent parquet should produce a clear error and exit 2."""
    monkeypatch.setattr(sys, "argv", ["train_cli.py", "NOSUCH", "H1"])
    monkeypatch.setenv("ALPHAMASTER_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc_info:
        train_cli.main()
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "不存在" in captured.out or "不存在" in captured.err


def test_main_inspect_parquet_generic_error_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Unexpected exception from inspect_parquet_file should produce clean error + exit 1."""
    monkeypatch.setattr(sys, "argv", ["train_cli.py", "FAKE", "H1"])
    monkeypatch.setenv("ALPHAMASTER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "train_cli.inspect_parquet_file",
        lambda _path: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(SystemExit) as exc_info:
        train_cli.main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "数据文件读取失败" in captured.err
    assert "boom" in captured.err


def test_main_happy_path_exits_0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Full mock-driven happy path: stub subprocess + all JSON readers."""

    # Create a fake data file (we never read it; inspect_parquet_file is stubbed).
    fake_parquet = tmp_path / "FAKE_H1.parquet"
    fake_parquet.write_bytes(b"")

    # Stub inspect_parquet_file.
    fake_info = {
        "data_file": str(fake_parquet),
        "filename": fake_parquet.name,
        "symbol": "FAKE",
        "timeframe": "H1",
        "bars": 1000,
        "years_h1": 0.16,
        "valid": True,
        "message": "",
    }
    monkeypatch.setattr(
        "train_cli.inspect_parquet_file", lambda _path: fake_info
    )

    # Stub run_training_subprocess to return 0.
    monkeypatch.setattr("train_cli.run_training_subprocess", lambda **_: 0)

    # Stub training_history_*.json reader.
    monkeypatch.setattr(
        "train_cli._read_history", lambda _sym: {"best_score": [1.0, 2.4], "val_score": [0.5, 1.8]}
    )

    # Stub best_*.json reader.
    monkeypatch.setattr(
        "train_cli._read_strategy",
        lambda _sym: {"formula_decoded": "alpha → close"},
    )

    # Stub training_time functions.
    monkeypatch.setattr(
        "train_cli._record_session",
        lambda **_: None,
    )
    summary = type("S", (), {"history_total_seconds": 52928})()
    monkeypatch.setattr(
        "train_cli._get_time_summary", lambda _sym, **_kw: summary
    )

    # Stub history_session_count (count of past sessions).
    monkeypatch.setattr("train_cli._history_session_count", lambda _sym: 8)

    # Inject current_step / train_steps from somewhere — patch the default.
    monkeypatch.setattr("train_cli._train_steps", lambda: 5000)
    monkeypatch.setattr("train_cli._current_step_from_history", lambda _sym: 5000)

    monkeypatch.setattr(sys, "argv", ["train_cli.py", "FAKE", "H1"])
    monkeypatch.setenv("ALPHAMASTER_DATA_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as exc_info:
        train_cli.main()

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "FAKE" in out
    assert "训练完成" in out
    assert "2.4000" in out  # best_score formatted


def test_find_latest_step_line_finds_latest() -> None:
    """Should return the LAST line containing [N/M], not the first."""
    content = (
        "some preamble\n"
        "[100/9000] step 100 metrics\n"
        "[200/9000] step 200 metrics\n"
        "[300/9000] step 300 metrics\n"
        "trailing noise\n"
    )
    result = train_cli._find_latest_step_line(content)
    assert result is not None
    assert "[300/9000]" in result
    assert "step 300" in result


def test_find_latest_step_line_returns_none_when_no_match() -> None:
    """No [N/M] markers → returns None."""
    content = "no markers here\njust text\n"
    assert train_cli._find_latest_step_line(content) is None


def test_find_latest_step_line_ignores_partial_brackets() -> None:
    """Only [N/M] (numeric) counts. [foo/bar] or [a/b] don't match."""
    content = "[abc/def] not a step\n[1/2] too small\n[1000/9000] real\n"
    result = train_cli._find_latest_step_line(content)
    assert result is not None
    assert "[1000/9000]" in result


def test_tail_progress_writes_latest_line(tmp_path: Path) -> None:
    """Background tailer should pick up the latest [N/M] line from the log file."""
    log = tmp_path / "out.log"
    log.write_text(
        "[100/9000] metrics A\n[200/9000] metrics B\n[300/9000] metrics C\n",
        encoding="utf-8",
    )
    buf = io.StringIO()
    stop_event = threading.Event()

    # Fast interval so the test runs in <1s
    tailer = threading.Thread(
        target=train_cli._tail_progress,
        args=(log, stop_event, 0.05),
        kwargs={"file": buf},
        daemon=True,
    )
    tailer.start()

    # Give the tailer ~150ms to run one cycle
    time.sleep(0.15)
    stop_event.set()
    tailer.join(timeout=1.0)

    out = buf.getvalue()
    assert "[300/9000]" in out
    assert "metrics C" in out


def test_tail_progress_handles_missing_log_file(tmp_path: Path) -> None:
    """If the log file doesn't exist yet (e.g. very early in training), tailer should silently no-op."""
    nonexistent = tmp_path / "missing.log"
    buf = io.StringIO()
    stop_event = threading.Event()

    tailer = threading.Thread(
        target=train_cli._tail_progress,
        args=(nonexistent, stop_event, 0.05),
        kwargs={"file": buf},
        daemon=True,
    )
    tailer.start()

    time.sleep(0.15)
    stop_event.set()
    tailer.join(timeout=1.0)

    # No crash, no spurious output
    assert buf.getvalue() == ""
