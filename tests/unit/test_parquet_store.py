from pathlib import Path
import pandas as pd
import pytest

from data_pipeline.parquet_store import ParquetStore, SchemaError


def test_save_load_roundtrip(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    df = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000, 1100],
        "amount": [100000.0, 111000.0],
    })
    store.save("600519.SH", df, period="1d")
    loaded = store.load("600519.SH", period="1d")
    assert loaded is not None
    assert list(loaded.columns) == ["time", "open", "high", "low", "close", "volume", "amount"]
    assert len(loaded) == 2


def test_path_for_format(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    assert store.path_for("600519.SH", "1d") == tmp_path / "600519.SH_1d.parquet"


def test_save_rejects_missing_columns(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    bad = pd.DataFrame({"time": [1], "close": [100.0]})
    with pytest.raises(SchemaError):
        store.save("600519.SH", bad, period="1d")


def test_append_bars_deduplicates_by_time(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    base = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02"]),
        "open": [100.0], "high": [102.0], "low": [99.0],
        "close": [101.0], "volume": [1000], "amount": [100000.0],
    })
    store.save("600519.SH", base, period="1d")
    new_row = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [100.5, 102.0], "high": [103.0, 104.0], "low": [99.5, 101.0],
        "close": [102.5, 103.0], "volume": [1100, 1200], "amount": [110000.0, 121000.0],
    })
    store.append_bars("600519.SH", new_row, period="1d")
    loaded = store.load("600519.SH", period="1d")
    assert len(loaded) == 2
    assert loaded.iloc[0]["close"] == 102.5  # 新版本覆盖


def test_list_cached(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    df = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02"]),
        "open": [100.0], "high": [102.0], "low": [99.0],
        "close": [101.0], "volume": [1000], "amount": [100000.0],
    })
    store.save("600519.SH", df, period="1d")
    store.save("000300.SH", df, period="1d")
    cached = store.list_cached()
    assert set(cached) == {("600519.SH", "1d"), ("000300.SH", "1d")}
