"""TQ 数据服务器（HTTP）客户端封装。

按 QTCLIENT.md 的接口约定：
  - 本机: http://localhost:8080
  - 外网(经 Cloudflare): https://redmitdx.gptalk.us.kg
  - 鉴权: TDX_API_KEY 非空时所有 /api/v1/* 强制 X-API-Key 头

只负责 HTTP 拉取 + DataFrame 规范化（与 TdxDataFetcher 长表 schema 对齐）。
落盘由调用方通过 ParquetStore 完成。
"""
from __future__ import annotations
import os
import time
from typing import Callable, Iterator, Sequence
import pandas as pd
import requests


class TdxServerError(RuntimeError):
    """TQ 数据服务器返回非 200 / 解析失败"""


class TdxServerUnavailable(RuntimeError):
    """网络不通 / 服务端不可达（区别于业务错误码）"""


# QTCLIENT.md 表格中 OHLCVA 在 HTTP 响应里的字段名 → 内部规范（小写）
_FIELD_MAP = {
    "Open": "open", "High": "high", "Low": "low",
    "Close": "close", "Volume": "volume", "Amount": "amount",
}


def _records_to_long(records: list[dict], code: str, idx: list) -> list[dict]:
    """records = [{code: value}, ...] → [{time, code, open, ...}, ...]"""
    out = []
    for i, rec in zip(idx, records):
        row = {"time": pd.Timestamp(i), "code": code}
        # rec 形如 {"600519.SH": 1426.0}; 单只股票 record 只有一个键
        for v in rec.values():
            # v 可能是单值或多值；这里假设单只
            break  # 实际字段值在 data[field] 的 records[i] 顺序上对应
        out.append(row)
    return out


class TdxServerFetcher:
    """TQ 数据服务器 HTTP 客户端。

    用法:
        cli = TdxServerFetcher(base=os.getenv("TQ_BASE", "http://localhost:8080"),
                               api_key=os.getenv("TDX_API_KEY"))
        df = cli.fetch_kline("600519.SH", start="20250101", end="20260101",
                             period="1d", dividend="front")
    """

    DEFAULT_BASE = "http://localhost:8080"
    DEFAULT_TIMEOUT = 30
    # QTCLIENT.md: 本机推荐 250/批; Cloudflare 免费隧道 100s 超时, 250 只易 timeout,
    # 这里默认 100, 经本机 (TQ_BASE=http://localhost:*) 可 env 提到 250。
    DEFAULT_MAX_BATCH = 100
    MAX_BATCH = 100           # 兼容旧字段; 实际优先用 DEFAULT_MAX_BATCH
    INTER_BATCH_SECONDS = 0.5
    RETRY_TIMES = 3
    RETRY_BACKOFF = 1.5       # 重试等待倍数

    def __init__(self,
                 base: str | None = None,
                 api_key: str | None = None,
                 timeout: int | None = None,
                 max_batch: int | None = None):
        self.base = (base or os.getenv("TQ_BASE") or self.DEFAULT_BASE).rstrip("/")
        # api_key 为 None 时仍读 env（QTCLIENT.md: TDX_API_KEY 非空才鉴权）
        self.api_key = api_key if api_key is not None else os.getenv("TDX_API_KEY")
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        # 本机 (localhost) 默认走 250, 经隧道走 100
        env_batch = os.getenv("TQ_BATCH_SIZE")
        if max_batch is not None:
            self.max_batch = max_batch
        elif env_batch and env_batch.isdigit():
            self.max_batch = int(env_batch)
        elif "localhost" in self.base or "127.0.0.1" in self.base:
            self.max_batch = 250
        else:
            self.max_batch = self.DEFAULT_MAX_BATCH
        self._session = requests.Session()
        if self.api_key:
            self._session.headers["X-API-Key"] = self.api_key

    # ── HTTP 层 ─────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict) -> dict:
        """单次 GET, 5xx 自动重试, 业务错误码透传为 TdxServerError。"""
        url = f"{self.base}{path}"
        last_err: Exception | None = None
        for attempt in range(self.RETRY_TIMES):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = TdxServerUnavailable(f"网络错误 {url}: {e}")
                time.sleep(self.RETRY_BACKOFF ** attempt)
                continue
            # 503 (TQ DLL 不可用) + 5xx 隧道错误 (Cloudflare 502/504/530) 都视为瞬时
            if resp.status_code == 503 or resp.status_code >= 500:
                last_err = TdxServerUnavailable(
                    f"HTTP {resp.status_code} from {url} (attempt {attempt+1}/{self.RETRY_TIMES})"
                )
                time.sleep(2.0 if resp.status_code == 503 else self.RETRY_BACKOFF ** attempt)
                continue
            if resp.status_code >= 400:
                # 业务错误: 400 / 401 / 422 —— 不重试, 直接抛
                snippet = resp.text[:200].replace("\n", " ")
                raise TdxServerError(
                    f"HTTP {resp.status_code} {url} params={params}: {snippet}"
                )
            return resp.json()
        raise last_err  # type: ignore[misc]

    def health(self) -> dict:
        """不要求 API key, 也不调 TQ。"""
        return self._get("/health", {})

    def health_ready(self) -> dict:
        """真调一次 TQ, 看通达信客户端在不在跑。"""
        # /health/ready 是公开端点, 鉴权不应触发; 但用 _get 也能跑通
        return self._get("/health/ready", {})

    # ── 业务快捷方法 ─────────────────────────────────────────────────────

    def list_stocks(self, market: str = "5", no_cache: bool = False) -> list[dict]:
        params: dict = {"market": market, "list_type": 1}
        if no_cache:
            params["no_cache"] = "true"
        return self._get("/api/v1/stocks", params).get("items", [])

    def sector_stocks(self, sector_code: str, block_type: int = 0) -> list[dict]:
        """sector_code 必须带后缀, 如 '881002.SH'。"""
        return self._get(f"/api/v1/sectors/{sector_code}/stocks",
                         {"block_type": block_type}).get("items", [])

    # ── K 线（核心）───────────────────────────────────────────────────────

    def iter_kline_batches(self,
                           codes: str | Sequence[str],
                           period: str = "1d",
                           start: str | None = None,
                           end: str | None = None,
                           count: int | None = None,
                           dividend: str = "front",
                           fields: str = "Open,High,Low,Close,Volume,Amount",
                           fill_data: bool = True,
                           ) -> Iterator[tuple[int, int, dict[str, pd.DataFrame], list[tuple[str, str]]]]:
        """按批流式拉 K 线, 每一批 HTTP 完成立刻 yield, 便于调用方边拉边落盘。

        Yields:
            (batch_idx, total_batches, parsed_dict, failed_in_batch)
            - batch_idx: 1-based
            - total_batches: 总批数
            - parsed_dict: 该批解析出的 {code: DataFrame}
            - failed_in_batch: 该批失败的 [(code, reason), ...]
        """
        if isinstance(codes, str):
            codes_list = [c.strip() for c in codes.split(",") if c.strip()]
        else:
            codes_list = list(codes)

        total_batches = (len(codes_list) + self.max_batch - 1) // self.max_batch
        for batch_idx, i in enumerate(range(0, len(codes_list), self.max_batch), start=1):
            chunk = codes_list[i:i + self.max_batch]
            params: dict = {
                "codes": ",".join(chunk),
                "period": period,
                "dividend": dividend,
                "fields": fields,
                "fill_data": str(fill_data).lower(),
            }
            if count is not None:
                params["count"] = count
            else:
                if start:
                    params["start"] = start
                if end:
                    params["end"] = end

            batch_failed: list[tuple[str, str]] = []
            parsed: dict[str, pd.DataFrame] = {}
            try:
                payload = self._get("/api/v1/kline", params)
                parsed = self._parse_kline_payload(payload)
                returned = set(parsed.keys())
                for code in chunk:
                    if code not in returned:
                        batch_failed.append((code, "empty in batch"))
            except (TdxServerError, TdxServerUnavailable) as e:
                for code in chunk:
                    batch_failed.append((code, str(e)[:120]))

            yield batch_idx, total_batches, parsed, batch_failed

            if i + self.max_batch < len(codes_list):
                time.sleep(self.INTER_BATCH_SECONDS)

    def fetch_kline(self,
                    codes: str | Sequence[str],
                    period: str = "1d",
                    start: str | None = None,
                    end: str | None = None,
                    count: int | None = None,
                    dividend: str = "front",
                    fields: str = "Open,High,Low,Close,Volume,Amount",
                    fill_data: bool = True,
                    progress_cb: Callable[[int, int, int], None] | None = None,
                    ) -> dict[str, pd.DataFrame]:
        """拉 K 线并按 code 拆成 {code: DataFrame[time, open, high, low, close, volume, amount]}。

        Args:
            codes: 单只 "600519.SH" 或多只 ["600519.SH", "000001.SZ"]
            period: 1d / 1m / 5m / ...
            start/end: YYYYMMDD 或 YYYYMMDDHHMMSS
            count: ≥ 1 时只取最近 N 条（无需 start/end）
            dividend: none / front / back
            fields: TQ 字段名（默认 OHLCVA）
            fill_data: 是否填充缺失交易日
        """
        if isinstance(codes, str):
            codes_list = [c.strip() for c in codes.split(",") if c.strip()]
        else:
            codes_list = list(codes)

        # QTCLIENT.md 坑: 一次别超过 ~600, 批量用 250 一批(本机) / 100(隧道)
        # iter_kline_batches 已经是分批 + 容错的, 这里只是把迭代结果收集起来
        all_dfs: dict[str, pd.DataFrame] = {}
        failed: list[tuple[str, str]] = []
        for batch_idx, total_batches, parsed, batch_failed in self.iter_kline_batches(
            codes=codes_list, period=period, start=start, end=end,
            count=count, dividend=dividend, fields=fields, fill_data=fill_data,
        ):
            all_dfs.update(parsed)
            failed.extend(batch_failed)
            if progress_cb:
                progress_cb(batch_idx, total_batches, len(parsed))
        if failed:
            all_dfs["_failed"] = failed  # type: ignore[assignment]
        return all_dfs

    @staticmethod
    def _parse_kline_payload(payload: dict) -> dict[str, pd.DataFrame]:
        """把 /api/v1/kline 响应拆成 {code: long-DataFrame}。

        响应形如:
          {
            "codes": ["600519.SH", "000001.SZ"],
            "data": {
              "Open":   {"index":[...], "columns":[...], "records":[...], "shape":[N,K]},
              "High":   {...},
              ...
            }
          }
        每个 field 的 records[i] 是个 dict: {code: value}。
        同一 time 上的各 field → 同一行, 共享 time/code。
        """
        data = payload.get("data") or {}
        if not data:
            return {}

        # 用第一个 field 拿 (index, codes, 行数), 后续 field 共享
        first_field = next(iter(data.values()))
        index = first_field["index"]
        codes = first_field["columns"]
        n = len(index)

        # 行骨架: 每行 (time, code)
        # codes 顺序在外层 records[i] 的 dict key 上 —— 单只时只有一个 key
        # 用第一只 code 的 keys 推断 columns 顺序
        first_record = first_field["records"][0] if first_field["records"] else {}
        ordered_codes = list(first_record.keys()) or list(codes)

        out: dict[str, pd.DataFrame] = {c: [] for c in ordered_codes}  # type: ignore[assignment]
        for code in ordered_codes:
            rows = []
            for i, ts in enumerate(index):
                row: dict = {"time": pd.Timestamp(ts), "code": code}
                rows.append(row)
            out[code] = rows  # type: ignore[assignment]

        for field_name, field_payload in data.items():
            target_col = _FIELD_MAP.get(field_name, field_name.lower())
            for i in range(n):
                ts = pd.Timestamp(index[i])
                rec = field_payload["records"][i]
                for code, value in rec.items():
                    # 找到该 code 对应的 row
                    if code in out and out[code]:  # type: ignore[operator]
                        # rows 是个 list[dict]
                        out[code][i][target_col] = value  # type: ignore[index]

        # list[dict] → DataFrame
        result: dict[str, pd.DataFrame] = {}
        for code, rows in out.items():
            df = pd.DataFrame(rows)
            if not df.empty:
                df["time"] = pd.to_datetime(df["time"])
                df = df.sort_values("time").reset_index(drop=True)
            result[code] = df
        return result