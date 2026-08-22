"""K线缓存：薄壳，委托 ParquetStore 处理 IO。

保留此文件是为了不破坏旧 import 路径。
旧 API（get / read_local / update_all）走 ParquetStore 实现。
"""
from __future__ import annotations
import warnings
from pathlib import Path
import pandas as pd

from .parquet_store import ParquetStore
from .config import PARQUET_ROOT, DEFAULT_PERIOD


warnings.warn(
    "data_pipeline.kline_cache 已废弃，请改用 data_pipeline.tdx_data_manager.TdxDataManager",
    DeprecationWarning,
    stacklevel=2,
)


_default_store: ParquetStore | None = None


def get_default_store() -> ParquetStore:
    global _default_store
    if _default_store is None:
        _default_store = ParquetStore(root=PARQUET_ROOT)
    return _default_store


def read_kline(code: str, period: str = DEFAULT_PERIOD) -> pd.DataFrame | None:
    return get_default_store().load(code, period)


def has_kline(code: str, period: str = DEFAULT_PERIOD) -> bool:
    return get_default_store().exists(code, period)


class KlineCache:
    """向后兼容的缓存类（已废弃，请用 TdxDataManager）。

    新逻辑：
    - read_local(code) → ParquetStore.load
    - get(code) → 直接读本地（不增量更新；TDX 数据由 fetch_daily.py 维护）
    """

    def __init__(self, root: Path | None = None):
        self.store = ParquetStore(root=root if root else PARQUET_ROOT)

    def read_local(self, code: str, period: str = DEFAULT_PERIOD) -> pd.DataFrame | None:
        return self.store.load(code, period)

    def get(self, code: str, mt5_connected: bool = False, force_refresh: bool = False,
            period: str = DEFAULT_PERIOD) -> pd.DataFrame | None:
        if force_refresh:
            return None
        return self.read_local(code, period)

    def update_all(self, symbols: list[str]) -> None:
        """已废弃：增量更新由 scripts/fetch_daily.py --refresh-missing 完成"""
        warnings.warn("KlineCache.update_all 已废弃，使用 scripts/fetch_daily.py", DeprecationWarning, stacklevel=2)
