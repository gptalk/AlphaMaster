"""通达信 TQ 接口封装（TdxW.exe + tqcenter.py）。

设计要点：
- import tqcenter 在首次 fetch 时才执行（不强制启动时依赖）
- TdxW.exe 未启动 → tq.initialize() 抛 RuntimeError，包装为 TdxNotAvailableError
- 批量拉取按 MAX_BATCH=50 分块
- TQ "假成功"返回空数据 → fetch_ohlcv 强制 len(df)>0 校验
"""
from __future__ import annotations
import importlib.util
import sys
import time
from pathlib import Path
from typing import Callable, Sequence
import pandas as pd


class TdxNotAvailableError(RuntimeError):
    """TdxW.exe 未运行 / tqcenter 不可用 / 登录过期"""


class TdxApiError(RuntimeError):
    """TQ API 调用返回异常或空数据"""


_TQ_FIELD_MAP = {
    "open": "Open", "high": "High", "low": "Low",
    "close": "Close", "volume": "Volume", "amount": "Amount",
}


class TdxDataFetcher:
    DEFAULT_PERIOD = "1d"
    DEFAULT_DIVIDEND_TYPE = "front"
    MAX_BATCH = 50
    RETRY_TIMES = 3
    RETRY_BASE_SECONDS = 2.0
    INTER_BATCH_SECONDS = 0.5

    def __init__(self, tq_path: str = r"C:\new_tdx_mock\PYPlugins\user"):
        self._tq_path = tq_path
        self._tq = None

    def _import_tq(self):
        """从 tq_path 加载 tqcenter.tq 模块。
        失败 → ImportError（被 _ensure_initialized 捕获并包装）。"""
        if str(self._tq_path) not in sys.path:
            sys.path.insert(0, str(self._tq_path))
        spec = importlib.util.find_spec("tqcenter")
        if spec is None:
            raise ImportError(f"tqcenter not found at {self._tq_path}")
        mod = importlib.import_module("tqcenter")
        return mod.tq

    def _ensure_initialized(self) -> None:
        if self._tq is not None:
            return
        try:
            self._tq = self._import_tq()
        except ImportError as e:
            raise TdxNotAvailableError(
                f"无法加载 tqcenter（路径={self._tq_path}）：{e}\n"
                "请确认 TdxW.exe 已安装并启动。"
            ) from e
        try:
            self._tq.initialize(__file__)
        except Exception as e:
            raise TdxNotAvailableError(
                f"tq.initialize 失败：{e}\n"
                "请确认 TdxW.exe 已启动并登录。"
            ) from e

    def _call_tq(self, codes: list[str], start: str, end: str,
                 period: str, dividend_type: str) -> pd.DataFrame:
        field_list = list(_TQ_FIELD_MAP.values())
        last_err: Exception | None = None
        for attempt in range(self.RETRY_TIMES):
            try:
                df = self._tq.get_market_data(
                    field_list=field_list,
                    stock_list=codes,
                    start_time=start,
                    end_time=end,
                    period=period,
                    dividend_type=dividend_type,
                )
                return df
            except Exception as e:
                last_err = e
                time.sleep(self.RETRY_BASE_SECONDS * (2 ** attempt))
        raise TdxApiError(
            f"TQ get_market_data 失败（重试 {self.RETRY_TIMES} 次）：{last_err}"
        )

    @staticmethod
    def _normalize_columns(df) -> pd.DataFrame:
        """统一 TQ 返回格式为 DataFrame[time, code, open, high, low, close, volume, amount]。

        TdxW.exe (tqcenter) 实际返回三种格式之一：
          A) dict[field_name, DataFrame] —— 批量，每只 code 是独立列
             例如 {Open: df[code], High: df[code], ...}
          B) DataFrame（单只直接返回，列 = field）
          C) dict[code, DataFrame]（理论上的另一种组织）
        """
        if df is None:
            return pd.DataFrame(columns=["time", "code", "open", "high", "low", "close", "volume", "amount"])

        # 格式 A：dict[field, DataFrame[code]] —— TdxW.exe 实际返回
        if isinstance(df, dict) and df and all(
            isinstance(v, pd.DataFrame) for v in df.values()
        ):
            return TdxDataFetcher._from_field_dict(df)

        # 格式 C：dict[code, DataFrame]
        if isinstance(df, dict):
            pieces = []
            for code, sub in df.items():
                if isinstance(sub, pd.DataFrame):
                    sub = sub.copy()
                    sub["code"] = code
                    pieces.append(sub)
                elif isinstance(sub, dict):
                    sub_df = pd.DataFrame(sub)
                    sub_df["code"] = code
                    pieces.append(sub_df)
            if not pieces:
                return pd.DataFrame(columns=["time", "code", "open", "high", "low", "close", "volume", "amount"])
            df = pd.concat(pieces, ignore_index=True)

        # 格式 B：DataFrame
        try:
            if len(df) == 0:
                return pd.DataFrame(columns=["time", "code", "open", "high", "low", "close", "volume", "amount"])
        except TypeError:
            return pd.DataFrame(columns=["time", "code", "open", "high", "low", "close", "volume", "amount"])

        rename = {v: k for k, v in _TQ_FIELD_MAP.items()}
        df = df.rename(columns=rename)
        if "time" not in df.columns:
            df = df.reset_index()
            first = df.columns[0]
            df = df.rename(columns={first: "time"})
        df["time"] = pd.to_datetime(df["time"])
        std_cols = ["time", "open", "high", "low", "close", "volume", "amount"]
        keep = [c for c in std_cols if c in df.columns]
        if "code" in df.columns:
            keep.insert(1, "code")
        return df[keep]

    @staticmethod
    def _from_field_dict(field_dict: dict) -> pd.DataFrame:
        """格式 A：{field_name: DataFrame[code]} → 长表 DataFrame。

        TdxW.exe 实际返回：
          {
            "Open":   DataFrame(cols=[code1, code2, ...], index=dates),
            "High":   ...,
            "Close":  ...,
            "Volume": ...,
            "Amount": ...,
          }
        每个 field 的 df：列名=code（多只），列值=该 field 的价格/量。索引=日期。
        """
        first_field_df = next(iter(field_dict.values()))
        if first_field_df is None or len(first_field_df) == 0:
            return pd.DataFrame(columns=["time", "code", "open", "high", "low", "close", "volume", "amount"])

        codes = list(first_field_df.columns)
        dates = first_field_df.index

        # 长表骨架：(time, code) 对每只 code 重复一次
        skeleton_rows = []
        for code in codes:
            for d in dates:
                skeleton_rows.append((pd.Timestamp(d), code))
        long_df = pd.DataFrame(skeleton_rows, columns=["time", "code"])

        # 合并每个 field
        field_rename = {v: k for k, v in _TQ_FIELD_MAP.items()}
        for field_name, field_df in field_dict.items():
            if field_df is None or len(field_df) == 0:
                continue
            target_col = field_rename.get(field_name, field_name.lower())
            if target_col in long_df.columns:
                continue
            # field_df 的每一列是一只 code；构造 (time, code) → value 的映射
            stack = field_df.stack().reset_index()  # cols: [date, code, value]
            stack.columns = ["time", "code", target_col]
            stack["time"] = pd.to_datetime(stack["time"])
            long_df = long_df.merge(stack, on=["time", "code"], how="left")

        std_cols = ["open", "high", "low", "close", "volume", "amount"]
        keep = ["time", "code"] + [c for c in std_cols if c in long_df.columns]
        long_df["time"] = pd.to_datetime(long_df["time"])
        return long_df[keep]

    def fetch_ohlcv(self, code: str, start: str, end: str,
                    period: str = DEFAULT_PERIOD,
                    dividend_type: str = DEFAULT_DIVIDEND_TYPE) -> pd.DataFrame:
        self._ensure_initialized()
        raw = self._call_tq([code], start, end, period, dividend_type)
        df = self._normalize_columns(raw)
        if len(df) == 0:
            raise TdxApiError(
                f"TQ 返回空数据：code={code}, start={start}, end={end}\n"
                "可能原因：TdxW.exe 未启动 / code 错 / 区间无交易日"
            )
        # 单只 fetch 不需要 code 列（已知）
        if "code" in df.columns:
            df = df.drop(columns=["code"])
        return df

    def fetch_universe(self, codes: Sequence[str], start: str, end: str,
                       period: str = DEFAULT_PERIOD,
                       progress_cb: Callable[[int, int, str], None] | None = None,
                       dividend_type: str = DEFAULT_DIVIDEND_TYPE,
                       ) -> dict[str, pd.DataFrame]:
        self._ensure_initialized()
        codes = list(codes)
        out: dict[str, pd.DataFrame] = {}
        failed: list[tuple[str, str]] = []
        total = len(codes)
        for i in range(0, total, self.MAX_BATCH):
            chunk = codes[i:i + self.MAX_BATCH]
            try:
                raw = self._call_tq(chunk, start, end, period, dividend_type)
                df_all = self._normalize_columns(raw)
                # TQ 长表：每行有 code 列或每列一个 code
                if "code" in df_all.columns:
                    for code in chunk:
                        sub = df_all[df_all["code"] == code]
                        if len(sub) > 0:
                            out[code] = sub.reset_index(drop=True)
                        else:
                            failed.append((code, "empty in batch"))
                else:
                    # 宽表：每只 code 一列；或单只结果
                    if len(chunk) == 1:
                        out[chunk[0]] = df_all.reset_index(drop=True)
                    else:
                        # 兜底：均匀分摊列
                        # TQ 宽表格式通常是 time + 各 code 的字段列（multi-index columns）
                        for j, code in enumerate(chunk):
                            failed.append((code, "wide-format-parse-skipped"))
            except TdxApiError as e:
                for code in chunk:
                    failed.append((code, str(e)))
            if progress_cb:
                progress_cb(min(i + self.MAX_BATCH, total), total, chunk[0] if chunk else "")
            if i + self.MAX_BATCH < total:
                time.sleep(self.INTER_BATCH_SECONDS)
        if failed and not out:
            raise TdxApiError(f"全部 {total} 只拉取失败，首例：{failed[0]}")
        out["_failed"] = failed  # type: ignore[assignment]
        return out

    def close(self) -> None:
        if self._tq is not None:
            try:
                self._tq.close()
            except Exception:
                pass
            self._tq = None
