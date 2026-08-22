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
    def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """TQ 返回列名 Open/High/...，统一小写化 + 标准化 time 列。
        保留 code 列（长表格式 TQ 会带 code）。"""
        if df is None or df.empty:
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
        # code 列若存在则保留在 time 之后
        if "code" in df.columns:
            keep.insert(1, "code")
        return df[keep]

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
