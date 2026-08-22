"""端到端：parquet → TdxDataManager → MT5FeatureEngineer → tensor。

需要 data/600519.SH_1d.parquet 存在（运行 scripts/fetch_daily.py --what 600519.SH 生成）。
跳过条件：文件不存在。
"""
from pathlib import Path
import pytest


@pytest.mark.skipif(
    not Path("data/600519.SH_1d.parquet").exists(),
    reason="parquet cache not present (run scripts/fetch_daily.py first)",
)
def test_parquet_to_feature_tensor():
    import torch
    from data_pipeline.tdx_data_manager import TdxDataManager
    from model_core.features import MT5FeatureEngineer

    mgr = TdxDataManager()
    try:
        raw_dict, df = mgr.build_training_matrix("600519.SH", "20200101", "20241231")
        assert raw_dict["close"].shape[0] == 1
        feat = MT5FeatureEngineer.compute_features(raw_dict)
        assert feat.shape[0] == 1  # N=1
        assert feat.shape[1] >= 30  # 至少 30 维特征（v3.0 扩展后实际 ~65）
        assert not torch.isnan(feat).any()
        assert not torch.isinf(feat).any()
    finally:
        mgr.close()


def test_tdx_modules_import():
    """无外部依赖的导入冒烟。"""
    from data_pipeline.parquet_store import ParquetStore, inspect_parquet_file
    from data_pipeline.universe import Universe
    from data_pipeline.dataset import align_multi_symbol
    from data_pipeline.tdx_fetcher import TdxDataFetcher, TdxNotAvailableError, TdxApiError
    from data_pipeline.tdx_data_manager import TdxDataManager
    from model_core.signal import compute_target_positions_stateless
    from model_core.backtest import MT5Backtest
    assert Universe.ANCHOR_STOCKS == Universe.ANCHOR_STOCKS
    assert callable(compute_target_positions_stateless)
    assert callable(MT5Backtest)


def test_universe_anchor_stocks_all_loadable_via_store(tmp_path):
    """30 个 anchor 品种代码全部可作为 path_for 输入（不实际拉数据）。"""
    from data_pipeline.parquet_store import ParquetStore
    store = ParquetStore(root=tmp_path)
    from data_pipeline.universe import Universe
    for code in Universe.ANCHOR_STOCKS:
        p = store.path_for(code, "1d")
        assert p.name == f"{code}_1d.parquet"
