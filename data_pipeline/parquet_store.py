"""Parquet 存储：{code}_{period}.parquet 命名契约。

所有 parquet 写读通过本模块，路径解析不硬拼字符串。
"""
from __future__ import annotations
from pathlib import Path
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
