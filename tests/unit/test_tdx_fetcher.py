import sys
import types
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from data_pipeline.tdx_fetcher import (
    TdxDataFetcher,
    TdxNotAvailableError,
    TdxApiError,
)


@pytest.fixture
def fake_tq_module():
    """伪造 tqcenter.tq 模块"""
    tq_mod = types.SimpleNamespace()
    tq_mod._initialized = False

    def initialize(path):
        tq_mod._initialized = True

    def get_market_data(field_list, stock_list, start_time, end_time,
                        period="1d", dividend_type="front"):
        if not tq_mod._initialized:
            raise RuntimeError("tq not initialized")
        if not stock_list:
            return pd.DataFrame()
        rows = []
        for code in stock_list:
            rows.append({
                "time": pd.Timestamp("2024-01-02"),
                "Open": 100.0, "High": 102.0, "Low": 99.0,
                "Close": 101.0, "Volume": 1000, "Amount": 100000.0,
                "code": code,
            })
        return pd.DataFrame(rows)

    def close():
        tq_mod._initialized = False

    tq_mod.initialize = initialize
    tq_mod.get_market_data = get_market_data
    tq_mod.close = close
    return tq_mod


def test_ensure_initialized_failure_raises_tdx_not_available(tmp_path):
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    with patch.object(fetcher, "_import_tq", side_effect=ImportError("no tqcenter")):
        with pytest.raises(TdxNotAvailableError):
            fetcher._ensure_initialized()


def test_fetch_ohlcv_returns_dataframe(fake_tq_module, tmp_path, monkeypatch):
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    monkeypatch.setattr(fetcher, "_import_tq", lambda: fake_tq_module)
    df = fetcher.fetch_ohlcv("600519.SH", "20240101", "20240110")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume", "amount"]


def test_fetch_ohlcv_empty_raises_tdx_api_error(fake_tq_module, tmp_path, monkeypatch):
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    monkeypatch.setattr(fetcher, "_import_tq", lambda: fake_tq_module)
    fake_tq_module._initialized = True
    with patch.object(fetcher, "_call_tq", return_value=pd.DataFrame()):
        with pytest.raises(TdxApiError):
            fetcher.fetch_ohlcv("600519.SH", "20240101", "20240110")


def test_fetch_universe_chunks_and_returns_dict(fake_tq_module, tmp_path, monkeypatch):
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    monkeypatch.setattr(fetcher, "_import_tq", lambda: fake_tq_module)
    codes = [f"{i:06d}.SH" for i in range(120)]
    out = fetcher.fetch_universe(codes, "20240101", "20240110")
    # 应包含 120 只 + "_failed" 键
    real_codes = [c for c in out.keys() if c != "_failed"]
    assert len(real_codes) == 120
    for code in real_codes[:3]:
        assert isinstance(out[code], pd.DataFrame)
        assert len(out[code]) >= 1
