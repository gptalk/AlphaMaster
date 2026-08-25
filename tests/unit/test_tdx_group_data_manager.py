"""tests/unit/test_tdx_group_data_manager.py

TDX 多品种数据管理器单元测试。

覆盖:
  - 缺失 parquet → WARNING + 跳过, 全部缺失才 raise
  - bars < MIN_BARS → 排除
  - 时间轴交集对齐 (dropna 行为)
  - target_ret / feat_tensor / bar_time 形状正确
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_pipeline.tdx_group_data_manager import TdxGroupDataManager


# ── 辅助 ────────────────────────────────────────────────────────────────────

def _write_parquet(store: Path, code: str, n_rows: int,
                   start_date: str = "2025-01-02", period: str = "1d") -> None:
    """在 {store}/{code}_{period}.parquet 写一份标准 OHLCV+amount+time。"""
    dates = pd.bdate_range(start=start_date, periods=n_rows)
    df = pd.DataFrame({
        "time":   dates,
        "open":   np.linspace(100.0, 110.0, n_rows),
        "high":   np.linspace(101.0, 111.0, n_rows),
        "low":    np.linspace(99.0, 109.0, n_rows),
        "close":  np.linspace(100.5, 110.5, n_rows),
        "volume": np.ones(n_rows) * 1000.0,
        "amount": np.ones(n_rows) * 100000.0,
    })
    (store / f"{code}_{period}.parquet").parent.mkdir(parents=True, exist_ok=True)
    out = store / f"{code}_{period}.parquet"
    df.to_parquet(out, engine="pyarrow", compression="snappy")


# ── TestMissingParquetIsSkipped ──────────────────────────────────────────────

class TestMissingParquetIsSkipped:
    """缺失 parquet 的 code → 跳过, 全部缺失才 raise。"""

    def test_missing_parquet_is_skipped(self, tmp_path):
        """CCC.SH parquet 不存在 → 不在 mgr.symbols 里 (行为即可, 不依赖 loguru capture)。"""
        store = tmp_path
        _write_parquet(store, "AAA.SH", 400)
        _write_parquet(store, "BBB.SH", 400)

        mgr = TdxGroupDataManager(parquet_root=store)
        mgr.load(["AAA.SH", "CCC.SH", "BBB.SH"])

        assert "AAA.SH" in mgr.symbols
        assert "BBB.SH" in mgr.symbols
        assert "CCC.SH" not in mgr.symbols

    def test_all_missing_raises_value_error(self, tmp_path):
        store = tmp_path
        mgr = TdxGroupDataManager(parquet_root=store)
        with pytest.raises(ValueError) as exc_info:
            mgr.load(["NOPE1.SH", "NOPE2.SH"])
        assert "No valid symbols" in str(exc_info.value)


# ── TestSymbolExclusionByMinBars ─────────────────────────────────────────────

class TestSymbolExclusionByMinBars:
    """bars < MIN_BARS 的 code 排除。"""

    def test_short_symbol_is_excluded(self, tmp_path):
        store = tmp_path
        _write_parquet(store, "LONG.SH", 500)
        _write_parquet(store, "SHORT.SH", 100)

        mgr = TdxGroupDataManager(parquet_root=store)
        mgr.load(["LONG.SH", "SHORT.SH"])

        assert "LONG.SH" in mgr.symbols
        assert "SHORT.SH" not in mgr.symbols

    def test_custom_min_bars(self, tmp_path):
        store = tmp_path
        _write_parquet(store, "AAA.SH", 50)

        mgr = TdxGroupDataManager(parquet_root=store, min_bars=20)
        mgr.load(["AAA.SH"])
        assert "AAA.SH" in mgr.symbols

        mgr2 = TdxGroupDataManager(parquet_root=store, min_bars=100)
        with pytest.raises(ValueError):
            mgr2.load(["AAA.SH"])


# ── TestIntersectionAlignment ────────────────────────────────────────────────

class TestIntersectionAlignment:
    """多 code 不同日期范围 → 对齐到交集。"""

    def test_intersection_takes_common_dates_only(self, tmp_path):
        store = tmp_path
        # 用 min_bars=50 避免默认 300 把这两个 short sample 排掉
        # AAA: 2025-01-02 起 130 工作日 (~2025-07-04 结束)
        _write_parquet(store, "AAA.SH", 130, start_date="2025-01-02")
        # BBB: 2025-04-01 起 110 工作日 (~2025-09-12 结束)
        _write_parquet(store, "BBB.SH", 110, start_date="2025-04-01")

        mgr = TdxGroupDataManager(parquet_root=store, min_bars=50)
        mgr.load(["AAA.SH", "BBB.SH"])

        T = mgr.raw_dict["open"].shape[1]
        # 交集 = BBB 起点 (2025-04-01) ~ AAA 终点 (~2025-07-04)
        # 约 Apr 22 + May 22 + Jun 21 + 头几天 Jul = ~67 工作日
        # 关键不变量: 67 < 110 (BBB 全长) AND 67 < 130 (AAA 全长)
        assert T < 110 and T < 130, (
            f"expected T < both single lengths, got T={T} "
            f"(AAA=130, BBB=110)"
        )
        # 起始日期应是 BBB 起点 (dropna 后第一行 = max(AAA start, BBB start))
        first_time = mgr.raw_dict["time"][0, 0].item()
        import datetime
        first_dt = datetime.datetime.fromtimestamp(first_time, tz=datetime.timezone.utc)
        assert first_dt >= pd.Timestamp("2025-04-01").tz_localize("UTC"), (
            f"intersection first date {first_dt} should be >= 2025-04-01"
        )
        assert mgr.symbols == ["AAA.SH", "BBB.SH"]


# ── TestTargetRetShape ───────────────────────────────────────────────────────

class TestTargetRetShape:
    """target_ret 形状正确, 最后 2 位为 0。"""

    def test_target_ret_shape_and_tail_zeros(self, tmp_path):
        store = tmp_path
        _write_parquet(store, "AAA.SH", 400)

        mgr = TdxGroupDataManager(parquet_root=store)
        mgr.load(["AAA.SH"])

        ret = mgr.target_ret
        assert ret.shape == (1, 400)
        assert ret.dtype == torch.float32
        # 最后 2 位为 0 (边界)
        assert ret[0, -1].item() == 0.0
        assert ret[0, -2].item() == 0.0


# ── TestFeatTensorShape ──────────────────────────────────────────────────────

class TestFeatTensorShape:
    """feat_tensor [N, F, T] 委托 MT5FeatureEngineer。"""

    def test_feat_tensor_is_n_f_t(self, tmp_path):
        store = tmp_path
        _write_parquet(store, "AAA.SH", 400)
        _write_parquet(store, "BBB.SH", 400)

        mgr = TdxGroupDataManager(parquet_root=store)
        mgr.load(["AAA.SH", "BBB.SH"])

        feat = mgr.feat_tensor
        N = 2
        T = mgr.raw_dict["open"].shape[1]
        assert feat.dim() == 3
        assert feat.shape[0] == N
        assert feat.shape[1] > 0
        assert feat.shape[2] == T


# ── TestBarTime ──────────────────────────────────────────────────────────────

class TestBarTime:
    """bar_time == raw_dict['time'][:, -1] (Unix 秒, int64)。"""

    def test_bar_time_is_last_bar(self, tmp_path):
        store = tmp_path
        _write_parquet(store, "AAA.SH", 300, start_date="2025-03-01")

        mgr = TdxGroupDataManager(parquet_root=store)
        mgr.load(["AAA.SH"])

        assert mgr.bar_time.shape == (1,)
        assert mgr.bar_time.dtype == torch.int64
        assert mgr.bar_time[0].item() == mgr.raw_dict["time"][0, -1].item()