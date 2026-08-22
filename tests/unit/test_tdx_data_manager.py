from pathlib import Path
from unittest.mock import MagicMock
import pandas as pd
import pytest
import torch

from data_pipeline.parquet_store import ParquetStore
from data_pipeline.tdx_data_manager import TdxDataManager


@pytest.fixture
def store(tmp_path):
    return ParquetStore(root=tmp_path)


@pytest.fixture
def fetcher():
    return MagicMock()


def _fake_ohlcv():
    return pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "open": [100.0, 101.0, 102.0],
        "high": [102.0, 103.0, 104.0],
        "low": [99.0, 100.0, 101.0],
        "close": [101.0, 102.0, 103.0],
        "volume": [1000, 1100, 1200],
        "amount": [100000.0, 110000.0, 120000.0],
    })


def test_get_or_fetch_cache_hit(store, fetcher):
    fetcher.fetch_ohlcv.return_value = _fake_ohlcv()
    mgr = TdxDataManager(store=store, fetcher=fetcher)
    df1 = mgr.get_or_fetch("600519.SH", "20240101", "20240131")
    assert fetcher.fetch_ohlcv.call_count == 1
    df2 = mgr.get_or_fetch("600519.SH", "20240101", "20240131")
    assert fetcher.fetch_ohlcv.call_count == 1
    assert len(df2) == 3


def test_bulk_ensure_cached_returns_status(store, fetcher):
    fetcher.fetch_universe.return_value = {
        "600519.SH": _fake_ohlcv(),
        "_failed": [("000001.SZ", "empty")],
    }
    mgr = TdxDataManager(store=store, fetcher=fetcher)
    status = mgr.bulk_ensure_cached(["600519.SH", "000001.SZ"], "20240101", "20240131")
    assert status["600519.SH"] == "fetched"
    assert status["000001.SZ"] == "failed"


def test_build_training_matrix_returns_tensor(store, fetcher):
    fetcher.fetch_ohlcv.return_value = _fake_ohlcv()
    mgr = TdxDataManager(store=store, fetcher=fetcher)
    tensor, df = mgr.build_training_matrix("600519.SH", "20240101", "20240131")
    assert tensor.shape == (1, 3, 5)
    assert isinstance(df, pd.DataFrame)
