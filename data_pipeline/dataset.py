"""多品种 OHLCV 对齐到共同 T 轴。

返回 MultiIndex(time, code) 长表，列固定为 OHLCV + amount。
- fill_method='ffill': 各 code 缺失日期前向填充；首行无法填充则留 NaN
- fill_method='dropna': 任何 code 在某日缺失 → 整日丢弃（仅保留所有 code 都有数据的日期）
"""
from __future__ import annotations
from datetime import date
from typing import Literal
import pandas as pd


class AlignmentError(Exception):
    """多品种对齐失败（输入为空）"""


OHLCV_COLS = ["open", "high", "low", "close", "volume", "amount"]


def align_multi_symbol(
    code_to_df: dict[str, pd.DataFrame],
    codes: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    fill_method: Literal["ffill", "dropna"] = "ffill",
) -> pd.DataFrame:
    if not code_to_df:
        raise AlignmentError("empty input")
    if codes is None:
        codes = list(code_to_df.keys())

    series: list[tuple[str, pd.DataFrame]] = []
    for code in codes:
        df = code_to_df[code]
        if df is None or df.empty:
            continue
        d = df.copy()
        if "time" in d.columns:
            d = d.set_index("time")
        if not isinstance(d.index, pd.DatetimeIndex):
            d.index = pd.to_datetime(d.index)
        d = d[OHLCV_COLS]
        series.append((code, d))

    if not series:
        raise AlignmentError("all inputs empty")

    # union 日期轴
    all_dates = sorted(set().union(*(d.index for _, d in series)))

    # 每个 code 独立 reindex + 可选 ffill，再 concat
    pieces = []
    for code, d in series:
        r = d.reindex(all_dates)
        if fill_method == "ffill":
            r = r.ffill()
        pieces.append(r)
        pieces[-1].index.name = "time"

    long = pd.concat(pieces, keys=codes[:len(pieces)], names=["code", "time"])
    long = long.reset_index()

    if fill_method == "dropna":
        # 标记每行 OHLCV 是否完整
        long["_complete"] = long[OHLCV_COLS].notna().all(axis=1)
        # 按 time 分组：所有 code 都 complete 才保留
        complete_times = long.groupby("time")["_complete"].all()
        keep = complete_times[complete_times].index
        long = long[long["time"].isin(keep)].drop(columns=["_complete"])

    if start is not None:
        long = long[long["time"] >= pd.to_datetime(start)]
    if end is not None:
        long = long[long["time"] <= pd.to_datetime(end)]

    return long.set_index(["time", "code"]).sort_index()
