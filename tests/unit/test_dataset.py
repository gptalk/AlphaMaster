from datetime import date
import pandas as pd
import pytest

from data_pipeline.dataset import align_multi_symbol, AlignmentError


def _df(rows):
    return pd.DataFrame({
        "time": pd.to_datetime([r[0] for r in rows]),
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows],
        "amount": [float(r[5]) * 100 for r in rows],
    }).set_index("time")


def test_aligns_two_symbols_to_union_axis_with_ffill():
    a = _df([("2024-01-02", 10, 11, 9, 10.5, 1000),
             ("2024-01-03", 10.5, 11.5, 10, 11, 1100)])
    b = _df([("2024-01-03", 20, 21, 19, 20.5, 2000),
             ("2024-01-04", 20.5, 21.5, 20, 21, 2100)])
    out = align_multi_symbol({"A.SH": a, "B.SH": b}, fill_method="ffill")
    assert isinstance(out.index, pd.MultiIndex)
    assert out.index.names == ["time", "code"]
    assert set(out.index.get_level_values("code").unique()) == {"A.SH", "B.SH"}
    # union = 3 个交易日（ffill 保留所有日期）
    unique_dates = out.index.get_level_values("time").unique()
    assert len(unique_dates) == 3


def test_aligns_with_dropna_keeps_only_common_dates():
    a = _df([("2024-01-02", 10, 11, 9, 10.5, 1000),
             ("2024-01-03", 10.5, 11.5, 10, 11, 1100)])
    b = _df([("2024-01-03", 20, 21, 19, 20.5, 2000),
             ("2024-01-04", 20.5, 21.5, 20, 21, 2100)])
    out = align_multi_symbol({"A.SH": a, "B.SH": b}, fill_method="dropna")
    unique_dates = out.index.get_level_values("time").unique()
    assert len(unique_dates) == 1  # 只有 2024-01-03 两边都有数据


def test_align_with_dropna_removes_partial_overlap():
    a = _df([("2024-01-02", 10, 11, 9, 10.5, 1000)])
    b = _df([("2024-01-03", 20, 21, 19, 20.5, 2000)])
    out = align_multi_symbol({"A.SH": a, "B.SH": b}, fill_method="dropna")
    assert len(out) == 0


def test_empty_input_raises():
    with pytest.raises(AlignmentError):
        align_multi_symbol({})
