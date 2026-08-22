"""Parquet 存储：{code}_{period}.parquet 命名契约。

所有 parquet 写读通过本模块，路径解析不硬拼字符串。
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import re
import pandas as pd


class SchemaError(Exception):
    """Parquet schema 不符合约定（缺列 / dtype 错）"""


class ParquetStore:
    SCHEMA = ["time", "open", "high", "low", "close", "volume", "amount"]

    def __init__(self, root: Path | str = Path("data")):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, code: str, period: str = "1d") -> Path:
        return self.root / f"{code}_{period}.parquet"

    def exists(self, code: str, period: str = "1d") -> bool:
        return self.path_for(code, period).exists()

    def load(self, code: str, period: str = "1d") -> pd.DataFrame | None:
        p = self.path_for(code, period)
        if not p.exists():
            return None
        return pd.read_parquet(p)

    def save(self, code: str, df: pd.DataFrame, period: str = "1d") -> None:
        self._validate(df)
        df.to_parquet(self.path_for(code, period), engine="pyarrow", compression="snappy")

    def list_cached(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for p in self.root.glob("*_*.parquet"):
            stem = p.stem
            if "_" not in stem:
                continue
            code, period = stem.rsplit("_", 1)
            out.append((code, period))
        return out

    def append_bars(self, code: str, new_df: pd.DataFrame, period: str = "1d") -> None:
        self._validate(new_df)
        existing = self.load(code, period)
        if existing is None:
            self.save(code, new_df, period)
            return
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time"], keep="last")
        combined = combined.sort_values("time").reset_index(drop=True)
        self.save(code, combined, period)

    @classmethod
    def _validate(cls, df: pd.DataFrame) -> None:
        missing = [c for c in cls.SCHEMA if c not in df.columns]
        if missing:
            raise SchemaError(f"missing columns: {missing}; required {cls.SCHEMA}")
        if not pd.api.types.is_datetime64_any_dtype(df["time"]):
            raise SchemaError(f"column 'time' must be datetime64, got {df['time'].dtype}")


# ── 文件名解析 + 检测工具 ─────────────────────────────────────────────────────

_FILENAME_RE = re.compile(r"^(?P<code>.+)_(?P<period>[^_]+)$")


def parse_parquet_filename(path: Path | str) -> tuple[str, str]:
    """从 {code}_{period}.parquet 文件名解析 code + period。

    Returns:
        (code, period) 例如 ('600519.SH', '1d')

    Raises:
        ValueError: 文件名格式不符合约定
    """
    p = Path(path)
    stem = p.stem
    m = _FILENAME_RE.match(stem)
    if not m:
        raise ValueError(f"文件名格式错误: {stem!r}（期望 {{code}}_{{period}}）")
    return m.group("code"), m.group("period")


def inspect_parquet_file(path: Path | str) -> dict[str, Any]:
    """检查 parquet 文件，返回 metadata 字典给 Web UI。

    Returns:
        dict with keys: path, symbol, timeframe, bars, years, first_time, last_time

    Raises:
        FileNotFoundError, ValueError
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    if p.suffix.lower() != ".parquet":
        raise ValueError("请选择 .parquet 文件")

    symbol, period = parse_parquet_filename(p)
    df = pd.read_parquet(p)
    bars = len(df)
    try:
        from config import Config
        min_bars = Config.MIN_BARS
    except Exception:
        min_bars = 300
    if bars < min_bars:
        raise ValueError(f"数据不足: {bars} bars（至少需要 {min_bars}）")

    years: float | None = None
    first_time = last_time = None
    if "time" in df.columns and len(df) > 1:
        try:
            t_min = pd.to_datetime(df["time"].min())
            t_max = pd.to_datetime(df["time"].max())
            first_time = str(t_min)
            last_time = str(t_max)
            if t_max > t_min:
                years = round((t_max - t_min).total_seconds() / (365.25 * 24 * 3600), 2)
        except Exception:
            pass

    return {
        "path": str(p.resolve()),
        "symbol": symbol,
        "timeframe": period,
        "bars": bars,
        "years": years,
        "first_time": first_time,
        "last_time": last_time,
    }
