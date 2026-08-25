"""多品种数据管理器（TDX parquet 版）。

API 与 MT5DataManager 对齐，供 AlphaEngine 训练管线直接接入。

与 MT5DataManager 的差异:
  - 数据源: 本地 parquet 文件 ({parquet_root}/{code}_{period}.parquet)，
    由 ParquetStore 读取；本阶段**纯离线**, 不连接 TQ
  - 数据频率: 单一 period(默认 '1d')；不同周期要构造新实例
  - 时间轴对齐: 用 dataset.align_multi_symbol(fill_method='dropna') 取交集,
    替代 MT5DataManager._align_timelines 的并集+ffill（与 MT5 行为一致）
  - Config.SYMBOLS 依赖: 不读; 训练时由 caller 显式传 symbols 列表

训练脚本接入:
    mgr = TdxGroupDataManager(parquet_root=Path("data/kline"))
    mgr.load(["600519.SH", "000001.SZ"])
    engine = AlphaEngine(data_manager=mgr, target_symbol="600519.SH")
"""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import torch
from loguru import logger

from config import Config
from data_pipeline.dataset import align_multi_symbol, OHLCV_COLS
from data_pipeline.parquet_store import ParquetStore
from model_core.features import MT5FeatureEngineer

# target_ret 复用 MT5DataManager 的 log return 公式, 行为完全一致
from data_pipeline.data_manager import MT5DataManager


class TdxGroupDataManager:
    """多品种数据管理器（TDX parquet 版），兼容 AlphaEngine 接口。

    公开属性:
        symbols        list[str]
        raw_dict       dict[field, Tensor[N, T]]
        feat_tensor    Tensor[N, F, T]
        target_ret     Tensor[N, T]
        bar_time       Tensor[N] (int64 Unix 秒)
    """

    SCHEMA = ["open", "high", "low", "close", "volume", "time"]

    def __init__(self,
                 parquet_root: Path | str = Path("data/kline"),
                 period: str = "1d",
                 min_bars: int | None = None):
        self.store = ParquetStore(root=Path(parquet_root))
        self.period = period
        self.min_bars = min_bars if min_bars is not None else Config.MIN_BARS

        # 缓存
        self._symbols: list[str] = []
        self._raw_dict: dict[str, torch.Tensor] | None = None
        self._target_ret: torch.Tensor | None = None

    # ── 公开方法 ────────────────────────────────────────────────────

    def load(self, symbols: Iterable[str] | None = None) -> None:
        """加载多品种 parquet 到内存。

        Args:
            symbols: 要加载的 code 列表；None 表示空（仅触发 __init__ 默认行为）。
                     不读 Config.SYMBOLS——训练入口必须显式传列表。
        """
        if symbols is None:
            logger.warning("TdxGroupDataManager.load() 未传 symbols, 无数据加载")
            self._symbols = []
            self._raw_dict = None
            self._target_ret = None
            return

        symbol_list = list(symbols)
        logger.info(f"Loading data for {len(symbol_list)} symbols from {self.store.root}")

        # ── 步骤 1: 逐 code 读 parquet, 跳过缺失 / bars 不足 ──────────
        code_to_df: dict[str, pd.DataFrame] = {}
        for code in symbol_list:
            df = self.store.load(code, self.period)
            if df is None or df.empty:
                logger.warning(
                    f"Symbol '{code}' parquet missing in {self.store.root}/"
                    f"{code}_{self.period}.parquet — skipping"
                )
                continue
            if len(df) < self.min_bars:
                logger.warning(
                    f"Symbol '{code}' has only {len(df)} bars "
                    f"(< MIN_BARS={self.min_bars}). Excluding."
                )
                continue
            code_to_df[code] = df

        if not code_to_df:
            raise ValueError(
                "No valid symbols loaded: all requested symbols are missing "
                f"from {self.store.root} or have fewer than {self.min_bars} bars."
            )

        self._symbols = list(code_to_df.keys())
        logger.info(f"Valid symbols ({len(self._symbols)}): {self._symbols}")

        # ── 步骤 2: 时间轴对齐(交集) ────────────────────────────────
        aligned = self._align_timelines(code_to_df)

        # ── 步骤 3: 构 raw_dict ───────────────────────────────────
        self._raw_dict = self._build_raw_dict(aligned)

        # ── 步骤 4: target_ret ─────────────────────────────────────
        self._target_ret = MT5DataManager._compute_target_ret(
            self._raw_dict["open"]
        )

        logger.info(
            f"Data loaded. raw_dict shape: N={len(self._symbols)}, "
            f"T={self._raw_dict['open'].shape[1]}"
        )

    def reload(self) -> None:
        """清空缓存, 用上次 load 的 symbols 列表重载。"""
        if self._symbols:
            self.load(self._symbols)
        else:
            logger.warning("reload() called before load(); nothing to do")

    # ── 属性(AlphaEngine 接口) ──────────────────────────────────────

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def raw_dict(self) -> dict[str, torch.Tensor]:
        self._ensure_loaded()
        return self._raw_dict  # type: ignore[return-value]

    @property
    def feat_tensor(self) -> torch.Tensor:
        """委托 MT5FeatureEngineer.compute_features()(与数据源无关)。"""
        self._ensure_loaded()
        return MT5FeatureEngineer.compute_features(self._raw_dict)  # type: ignore[arg-type]

    @property
    def target_ret(self) -> torch.Tensor:
        self._ensure_loaded()
        return self._target_ret  # type: ignore[return-value]

    @property
    def bar_time(self) -> torch.Tensor:
        """每品种最后一根 K 线的 Unix 秒(int64), [N]"""
        self._ensure_loaded()
        raw = self._raw_dict  # type: ignore[assignment]
        if "time" in raw:
            return raw["time"][:, -1].long()
        return torch.zeros(len(self._symbols), dtype=torch.int64)

    # ── 内部辅助 ───────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._raw_dict is None:
            raise RuntimeError(
                "Data not loaded. Call TdxGroupDataManager.load() first."
            )

    def _align_timelines(
        self, code_to_df: dict[str, pd.DataFrame]
    ) -> dict[str, pd.DataFrame]:
        """多品种对齐到共同 T 轴(交集, 严格无未来信息)。

        用 dataset.align_multi_symbol(fill_method='dropna'):
          - 返回 MultiIndex(time, code) 长表
          - 任何 code 在某日缺失 -> 整日丢弃(只保留所有 code 都有数据的日期)
        然后转回 {code: DataFrame[time-indexed]} 给 _build_raw_dict。

        注意: dataset.align_multi_symbol 默认用 union+ffill, 我们传 dropna
        改为交集——这与 MT5DataManager._align_timelines 的默认行为(intersection
        否则 union+ffill) 一致, 彻底消除因休市 ffill 导致的"重复K线"问题。
        """
        long_df = align_multi_symbol(
            code_to_df,
            codes=list(code_to_df.keys()),
            fill_method="dropna",
        )
        # long_df: MultiIndex[time, code], 列 = OHLCV_COLS (+ 隐含 time/code)
        # 转回 dict[code] -> time-indexed DF
        aligned: dict[str, pd.DataFrame] = {}
        for code in code_to_df.keys():
            try:
                sub = long_df.xs(code, level="code")[OHLCV_COLS].copy()
            except KeyError:
                # dropna 后该 code 一个有效 bar 都没了
                logger.warning(
                    f"Symbol '{code}' 全部日期与其他 code 不重叠, 对齐后为空"
                )
                continue
            aligned[code] = sub
        if not aligned:
            raise ValueError(
                "Align intersection is empty: no common trading dates across "
                "loaded symbols."
            )
        logger.info(
            f"Aligned intersection: {len(next(iter(aligned.values())))} bars "
            f"(from union of {sum(len(df) for df in code_to_df.values())} total)"
        )
        return aligned

    def _normalize_time(self, time_values) -> np.ndarray:
        """把 parquet 的 time 列统一到 Unix 秒(int64)。

        实现:
          - datetime64 → 通过 .view('int64') + 单位换算
          - 数值列 → 直接判定量级 (秒 vs 秒/1000)

        单位: align_multi_symbol 走 reindex/concat 后, dtype 可能是 us/ns/ms,
        .unit 属性在新 numpy 上被移除, 从 dtype.str 用正则解析更稳。
        """
        import re
        if hasattr(time_values, "dtype") and hasattr(time_values.dtype, "kind"):
            if time_values.dtype.kind == "M":
                m = re.search(r"\[(\w+)\]", str(time_values.dtype))
                unit = m.group(1) if m else "ns"
                ns_per_unit = {
                    "ns": 1_000_000_000,
                    "us": 1_000_000,
                    "ms": 1_000,
                    "s":  1,
                }.get(unit, 1_000_000_000)
                int64_view = time_values.view("int64")
                return (int64_view // ns_per_unit).astype("int64")

        # 数值列(已是 int/float)
        if hasattr(time_values, "astype"):
            arr = time_values.astype("int64")
            if arr.max() < 10_000_000:           # 秒/1000 (毫秒单位)
                return arr * 1000
            return arr                            # 已是秒
        return np.asarray(time_values, dtype="int64")

    def _build_raw_dict(
        self, aligned: dict[str, pd.DataFrame]
    ) -> dict[str, torch.Tensor]:
        """对齐后 DF -> {field: Tensor[N, T]}。"""
        fields = ["open", "high", "low", "close", "volume"]
        n = len(self._symbols)
        t = next(iter(aligned.values())).shape[0]

        raw_dict: dict[str, torch.Tensor] = {}
        for field in fields:
            rows = [aligned[s][field].to_numpy(dtype="float32") for s in self._symbols]
            raw_dict[field] = torch.tensor(np.array(rows), dtype=torch.float32)  # [N, T]

        # time: int64 Unix 秒, [N, T]
        time_rows = [self._normalize_time(aligned[s].index.values) for s in self._symbols]
        raw_dict["time"] = torch.tensor(np.array(time_rows), dtype=torch.int64)

        assert raw_dict["open"].shape == (n, t)
        return raw_dict