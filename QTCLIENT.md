# TQ 数据服务器 — 客户端对接指南

本文档面向 **客户端开发者** —— 你没有装通达信,只拿到本服务地址(本机或经 Cloudflare 隧道),需要从这台 Windows 机器拿 A 股行情 / 财务 / 板块数据。

服务地址:
- 本机:`http://localhost:8080`
- 外网(经 Cloudflare):`https://redmitdx.gptalk.us.kg`
- 交互文档:`http://localhost:8080/docs`(Swagger UI)
- OpenAPI schema:`http://localhost:8080/openapi.json`

---

## 5 分钟上手

```bash
# 1. 探活:不要求 API key,也不会真调通达信
curl http://localhost:8080/health
# {"status":"ok","service":"tdxData","version":"..."}

# 2. 就绪:会真调一次 TQ,看通达信客户端在不在跑
curl http://localhost:8080/health/ready
# {"status":"ok","tq_reachable":true,"a_share_count":5557,...}

# 3. 拉一只股票的日 K 线(前复权)
curl "http://localhost:8080/api/v1/kline?codes=600519.SH&period=1d&start=20250101&end=20260101&dividend=front"

# 4. 配了 TDX_API_KEY 时,所有 /api/v1/* 请求都要带这个头:
curl -H "X-API-Key: your-secret" "http://localhost:8080/api/v1/stocks?market=5"
```

> **鉴权**:`TDX_API_KEY` 非空时,`/api/v1/*` 所有路由强制 `X-API-Key` 头;401 时返回
> `WWW-Authenticate: ApiKey realm="tdxData"`。`/health` 和 `/health/ready` 公开。
>
> **没装 API Key?** 服务默认不鉴权,直接调。`/admin/config` 永远返回 key 掩码(不会泄漏原值)。

---

## 接口目录

| 模块 | 路由前缀 | 关键端点 | 说明 |
|------|---------|---------|------|
| 健康 | `/health` `/health/ready` `/` | GET | 元数据 + TQ 在线探测 |
| 股票 | `/api/v1/stocks` | GET `/` `/<code>/info` `/<code>/snapshot` `/<code>/more` `/<code>/relation` `/<code>/gbinfo` | 列表 / 基础信息 / 快照 / 更多 / 关系 / 股本 |
| K线 | `/api/v1/kline` | GET `/` `/snapshot` `/divid/<code>` | 多股批量 + 复权 + 周期 + 实时快照(26 字段含盘口) + 分红因子 + 分时(1m/5m) |
| 板块 | `/api/v1/sectors` | GET `/` `/user` `/<code>/stocks` | 板块列表 / 用户板块 / 板块成分股 |
| 财务 | `/api/v1` | GET `/finance/<code>` `/gpjy/<codes>` `/bkjy/<codes>` `/scjy` `/gpjy_one/<codes>` | 5 个字段集 |
| 元数据 | `/api/v1` | GET `/trading-dates` `/kzz/<code>` `/ipo` `/track-etf/<code>` | 交易日 / 可转债 / IPO / ETF 跟踪 |
| 推送 | `/api/v1/notify` | POST `/message` `/file` `/warn` `/user-block` `/sector[/<code>[/clear]]` DELETE `/sector/<code>` PUT `/sector/<code>` | 改通达信客户端状态 |
| 管理 | `/api/v1/admin` | GET `/status` `/metrics` `/config` `/logs` `/logs/list` `/tq/test` POST `/restart` `/cache/refresh` `/kline/refresh` `/cache/invalidate` `/tq/close` `/download` | 监控 / 重启 / 缓存 |

---

## 响应格式:DataFrame → JSON 的约定

行情/财务接口的 DataFrame 一律序列化成:

```json
{
  "index":   ["2025-01-02T00:00:00", "2025-01-03T00:00:00", ...],
  "columns": ["600519.SH", "000001.SZ"],
  "records": [
    {"600519.SH": 1426.0, "000001.SZ": 11.2},
    {"600519.SH": 1430.5, "000001.SZ": 11.3}
  ],
  "shape": [2, 2]
}
```

`NaN` / `Inf` → `null`;`pd.Timestamp` / `datetime` / `date` → ISO 8601 字符串;numpy 标量拆成 Python 原生类型。中文键名原样保留,不转 `\uXXXX`。

Python 客户端拿到后:

```python
import pandas as pd
data = response.json()["data"]
close_df = pd.DataFrame(data["Close"]["records"], index=pd.to_datetime(data["Close"]["index"]))
```

---

## 股票

### `GET /api/v1/stocks` — 列表(默认走 5 分钟缓存)

`market` 字符串参数:

| 类别 | 代码 | 含义 | 实测条目 |
|------|------|------|---------|
| 股票 | `0` | 自选股 | 需先在客户端设置 |
| 股票 | `1` | 持仓股 | 需先在客户端设置 |
| **股票** | **`5`** | **所有 A 股(默认)** | ~5558 |
| 股票 | `6` | 上证指数成份股 | ~2226 |
| 股票 | `7` | 上证主板 | ~1701 |
| 股票 | `8` | 深证主板 | ~1494 |
| 股票 | `21` | 含 H 股的 A 股 | ~202 |
| 股票 | `22` | 含可转债的 A 股 | ~315 |
| 股票 | `50` | 沪深 A 股 | ~5450 |
| 行业 | `10` | 所有板块指数 | ~588(混行业+概念+风格+地区) |
| **行业** | **`11`** | **缺省行业(申万二级)** | ~128(干净) |
| 行业 | `12` | 概念板块 | ~269 |
| 行业 | `13` | 风格板块 | ~158 |
| 行业 | `14` | 地区板块 | ~32 |
| 行业 | `15` | 缺省行业 + 概念 | — |
| 行业 | `16` | 研究行业一级 | ~30 |
| 宽基 | `23` | 沪深 300 | ~300 |
| 宽基 | `24` | 中证 500 | ~500 |
| 宽基 | `25` | 中证 1000 | ~1000 |
| 宽基 | `26` | 国证 2000 | ~2000 |
| 宽基 | `27` | 中证 2000 | ~2000 |
| 宽基 | `28` | 中证 A500 | ~500 |
| 基金 | `30`~`36` | REITs / ETF / 可转债 / LOF / 全部 / 沪深 / T+0 | — |
| 板块细分 | `49`~`53` | 金融类 / 创业板 / 科创板 / 北交所 | — |

**请求**:`GET /api/v1/stocks?market=11&list_type=1&no_cache=false`
**响应**:
```json
{"market":"11","list_type":1,"count":128,"items":[{"Code":"881002.SH","Name":"煤炭开采"}, ...]}
```

> **坑**:`market=5` 一次拉 ~5558 条是 RPC 慢活;用 `no_cache=true` 只在确实要刷新时传。

### `GET /api/v1/stocks/<code>/info` — 基础信息

总股本 / 流通股本 / 资产 / 负债等。

```bash
curl "http://localhost:8080/api/v1/stocks/600519.SH/info?fields=TotalShare,FloatShare"
```

### `GET /api/v1/stocks/<code>/snapshot` — 实时行情快照

价格 / 涨跌 / 成交量等。不带 `fields` 时返全字段(字典)。

### `GET /api/v1/kline/snapshot?codes=...` — 批量快照

多只股票一次拿实时行情;带 `errors` 字段记录部分失败的 code。

```bash
curl "http://localhost:8080/api/v1/kline/snapshot?codes=600519.SH,000001.SZ"
```

### `GET /api/v1/stocks/<code>/relation` — 所属板块

返该股票所在的所有板块 / 行业 / 概念。

### `GET /api/v1/stocks/<code>/gbinfo` — 股本数据(按报告期)

`dates=YYYYMMDD,YYYYMMDD` 或 `count=N` 拿最近 N 个报告期。

---

## K 线

### `GET /api/v1/kline` — 多股批量历史行情

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `codes` | ✓ | — | 逗号分隔,如 `600519.SH,000001.SZ` |
| `period` | ✗ | `1d` | `1m` / `5m` / `15m` / `30m` / `1h` / `1d` / `1w` / `1mon` / `45d` / `1q` / `1y` / `10m` |
| `start` | ✗ | — | `YYYYMMDD` 或 `YYYYMMDDHHMMSS` |
| `end` | ✗ | — | 同上 |
| `count` | ✗ | `-1` | `>=1` 时只取最近 N 条(无需 start/end);`count=0` 或 < -1 返 400 |
| `dividend` | ✗ | `none` | `none` / `front` / `back`(不复权 / 前复权 / 后复权) |
| `fields` | ✗ | `Open,High,Low,Close,Volume,Amount` | 逗号分隔 TQ 字段名(不区分大小写) |
| `fill_data` | ✗ | `true` | 是否填充缺失交易日 |

**坑**:
- `codes` 一次别超过 **~600 支**。6000 支会 timeout。批量拉盘用 250 一批。
- `period` 传 `999d` 这种非白名单值返 400。
- 响应里 TQ 内部字段(`ForwardFactor` / `VolInStock`)会被自动剥掉,你看到的永远是 OHLCVA。
- TQ 内部错误码(`{error: -N, msg: ...}`)会被服务端转成 **400 + 透传 msg**,不会再让客户端看到神秘的空 data。

**完整响应示例**(简化):

```json
{
  "codes": ["600519.SH"],
  "fields": ["Open","High","Low","Close","Volume","Amount"],
  "period": "1d",
  "dividend": "front",
  "data": {
    "Open":   {"index":["2025-01-02T00:00:00", ...], "columns":["600519.SH"], "records":[...], "shape":[N,1]},
    "Close":  {...}
  }
}
```

### 分时数据(分钟 K 线 / 当日 tick 快照)

**分时 K 线 = `period=1m` 或 `5m`**。这是分钟聚合 K 线,字段和日线一致:

| period | 单股全天根数 | 单股响应大小 | 用途 |
|--------|-------------|-------------|------|
| `1m` | 240 根(4h × 60) | ~67 KB | 最细的"分时图",做日内策略 |
| `5m` | 48 根 | ~14 KB | 折中,带宽友好 |
| `15m` / `30m` | 16 / 8 根 | — | 当日波段分析 |

```bash
# 单股当日 1 分钟线
curl "http://localhost:8080/api/v1/kline?codes=600519.SH&period=1m&start=20260821&end=20260821"

# 多股当日 1 分钟线(行=时间,列=股票,Time-Aligned)
curl "http://localhost:8080/api/v1/kline?codes=600519.SH,000001.SZ,688318.SH&period=1m&start=20260821&end=20260821"

# 最近 60 根 1m(不需要 start/end)
curl "http://localhost:8080/api/v1/kline?codes=600519.SH&period=1m&count=60"
```

**实测**(2026-08-21 周五,3 只股票):
- 单股 1m:240 行 × 6 字段,~67 KB,~80 ms
- 3 股 1m:240 × 3 = 720 cells,~127 KB,~130 ms
- 5 个交易日 1m:1200 行 × 6 字段,~337 KB,~210 ms
- `fields=Close,Volume` 过滤:67 KB → 22 KB(约 1/3 带宽)
- 14:58 / 14:59 / 15:00 三根 Volume 经常为 0 —— 是 A 股收盘集合竞价期间没有 1 分钟成交量柱的 TQ 数据行为,不是 bug

**1m 与 snapshot 对账**(同时间点):
- 1m 末根(15:00)Close = snapshot.Now(完美对齐)
- snapshot.LastClose = 当日开盘 Open(同时 = 前收盘)
- snapshot.Volume = TQ 当前累计成交量(可能小于 1m 当日累计,因为实时抓取时还在交易)

**真正的秒级 tick / 逐笔成交**:
本机 TQ DLL(`tqcenter.py`)的 `valid_periods` 白名单**不包含 `tick`** —— 即便客户端传
`period=tick` 服务端也会先 400 拦掉;若绕过白名单直接走到 DLL,DLL 自己会返
`{error: -5, msg: '周期格式错误'}`。当前可用最细粒度就是 `1m`。

实时盘口(十档 + 内外盘 + TickDiff)走快照:

```bash
curl "http://localhost:8080/api/v1/kline/snapshot?codes=600519.SH"
# 返 26 个字段:Now / Open / High / Low / LastClose / Volume / NowVol / Amount /
#   Inside / Outside / TickDiff / InOutFlag / Jjjz / Buyp[5] / Buyv[5] / Sellp[5] / Sellv[5] / UpHome / DownHome ...
```

### `GET /api/v1/kline/divid/<code>` — 分红送配 / 复权因子

不带 `start`/`end` 拿全部历史分红记录。

---

## 板块

### `GET /api/v1/sectors?list_type=N`

| `list_type` | TQ 实际返 | 响应里 Name 字段 |
|------|------|------|
| `0`(默认) | `[str]` —— 587 个纯代码 | 用 Code 兜底(没真实名称) |
| `1` | `[dict]` —— 587 个 `{Code, Name}` | 真实名称 |

> 其它值(`11` / `16` / `2`)TQ 默默返空,服务端直接 **400 拦截**。

### `GET /api/v1/sectors/user` — 用户自定义板块

返通达信客户端里设的自定义板块列表,形如:

```json
{"count":5,"items":[{"Code":"ZFXG","Name":"涨幅选股"}, {"Code":"LZXG","Name":"连涨选股"}, ...]}
```

### `GET /api/v1/sectors/{code}/stocks` — 板块成分股

| 参数 | 默认 | 说明 |
|------|------|------|
| `block_type` | `0` | `0`=板块代码(申万/通达信);`1`=客户端自定义简称(如 ZFXG);`2`=期货前缀 |
| `list_type` | `0` | 仅对 `block_type=0` 生效;申万场景实测无影响 |

**坑**:
- **申万行业代码必须带 `.SH` 后缀**(`881002.SH` 正确,`881002` 返空)。
- `block_type=1` 用客户端自定义板块简称时,先 `GET /sectors/user` 看有哪些可用。
- 空用户板块(如 `PROJECTION`)TQ 返 `[]`,不是错误。
- `block_type` 不在 `{0,1,2}` → 400。

**示例**:
```bash
# 申万煤炭开采 25 只
curl "http://localhost:8080/api/v1/sectors/881002.SH/stocks"

# 自定义板块 ZFXG(涨幅选股)18 只
curl "http://localhost:8080/api/v1/sectors/ZFXG/stocks?block_type=1"

# ETF 篮子 880081.SH 包含 2 只 ETF
curl "http://localhost:8080/api/v1/sectors/880081.SH/stocks"
```

---

## 财务数据(5 个端点)

**通用规则**:
- `start` / `end` 留空时默认过去 1 年 ~ 今天(覆盖最近 4 个季度)。
- 不传 `fields` 时多数端点只返基础时间列,数字字段必须显式指定字段代码。
- 字段代码大小写不敏感,TQ 自己 normalize 成大写。

### `GET /api/v1/finance/<code>` — 基础 / 专业财务数据

**字段**:`Fn1..Fn584`(580 个),完整表见通达信官方:
<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html>

**常用精选**:

| ID | 名称 | 单位 |
|----|------|------|
| `FN1` | 基本每股收益 | 元 |
| `FN2` | 扣非每股收益 | 元 |
| `FN4` | 每股净资产 | 元 |
| `FN6` | 净资产收益率(ROE) | % |
| `FN134` | 净利润 | 元 |
| `FN183` | 营业收入增长率 | % |
| `FN184` | 净利润增长率 | % |
| `FN197` | 净资产收益率 | % |
| `FN202` | 销售毛利率 | % |
| `FN210` | 资产负债率 | % |
| `FN230` | 营业收入 | 元 |
| `FN232` | 归属于母公司所有者的净利润 | 元 |

```bash
curl "http://localhost:8080/api/v1/finance/600519.SH?fields=FN1,FN197,FN230&start=20250101&end=20260101"
```

多只股票:`?codes=600519.SH,000001.SZ&fields=FN1`(覆盖 path 里的 code)。

### `GET /api/v1/gpjy/<codes>` — 股票交易数据(股东 / 融资融券 / 分红)

**字段**:`GP1..GP52`,完整表:
<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10muc82r55k.html>

| ID | 名称 | 说明 |
|----|------|------|
| `GP1` | 股东人数 | 户 |
| `GP2` | 龙虎榜买入/卖出总计 | 万元 |
| `GP3` | 融资融券-融资余额/融券余量 | 万元/股 |
| `GP6` | 陆股通持股量 | 股 |
| `GP16` | 总市值 | 万元 |
| `GP21` | 股息率 | % |

返回的不是 DataFrame,是 `{code: {GPx: [{Date, Value}]}}` 原始结构。

### `GET /api/v1/bkjy/<codes>` — 板块交易数据

**字段**:`BK5..BK19`,完整表:
<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10p0ncmp5mc.html>

| ID | 名称 |
|----|------|
| `BK5` | 市盈率 TTM |
| `BK6` | 市净率 MRQ |
| `BK10` | 板块总市值(亿元) |
| `BK12` | 涨停家数 |
| `BK16` | 沪股通 / 深股通资金流入(亿元) |

`codes` 接板块 ID,如 `881002.SH`(必须带 `.SH`)。

### `GET /api/v1/scjy` — 市场级指标

**字段**:`SC1..SC30`,完整表:
<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10p8op6ia9g.html>

| ID | 名称 |
|----|------|
| `SC1` | 沪深京融资余额 / 融券余额(万元) |
| `SC3` | 沪深京涨停个数 |
| `SC8` | ETF 基金份额 / 净申赎(亿份) |
| `SC13` | 市场总分红额(亿元) |
| `SC14` | 市场总募资额(亿元) |

无代码参数(市场级);`start` / `end` 不默认,按需传。

### `GET /api/v1/gpjy_one/<codes>` — 股票单个数据快照

**字段**:`GO1..GO47`,完整表:
<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10pk3rsg044.html>

| ID | 名称 | 单位 |
|----|------|------|
| `GO1` | 发行价 | 元 |
| `GO3` | 一致预期目标价 | 元 |
| `GO5` | 一致预期 T 年每股收益 | 元 |
| `GO26` | 最新解禁日 | YYMMDD |
| `GO27` | 最新解禁数量 | 万股 |
| `GO33` | 最新总股本 | 万股 |
| `GO34` | 最新实际流通 A 股 | 万股 |

返回 `{code: {GOx: val}}`,**没有时间参数**,值是当前最新值。`fields` 不能为空。

---

## 元数据

### `GET /api/v1/trading-dates?market=SH`

交易日历(需先在通达信客户端下载上证指数 999999 的盘后数据)。

```bash
# 最近 10 个交易日
curl "http://localhost:8080/api/v1/trading-dates?market=SH&count=10"

# 指定区间
curl "http://localhost:8080/api/v1/trading-dates?market=SH&start=20250101&end=20250131"
```

### `GET /api/v1/kzz/<code>` — 可转债基础数据

### `GET /api/v1/ipo?ipo_type=0&ipo_date=1` — 新股 / 新债申购

`ipo_type`: `0`=新股,`1`=新债,`2`=全部。`ipo_date`: `0`=今日,`1`=今日及以后。

### `GET /api/v1/track-etf/<code>` — 跟踪指数的 ETF

`code` 是指数代码(如 `000300.SH`),返跟踪它的 ETF 列表。

---

## 推送(只对本机通达信客户端有效)

这些端点会改本机 TdxW.exe 的状态(显示消息、创建板块、推送预警),**不会自动下单**,由人工决定。

### `POST /api/v1/notify/message?msg=...`

推一条消息到通达信策略管理器(给用户看)。

### `POST /api/v1/notify/warn`

推预警信号。`stock_list` / `time_list` / `price_list` 等都是**逗号分隔**且**长度必须一致**:

```bash
curl -X POST "http://localhost:8080/api/v1/notify/warn" \
  --data-urlencode "stock_list=600519.SH,000001.SZ" \
  --data-urlencode "time_list=20260820150000,20260820150000" \
  --data-urlencode "price_list=1430.5,11.3" \
  --data-urlencode "reason_list=突破前高,跌停预警"
```

### `POST /api/v1/notify/user-block?block_code=ZXG&stocks=600519.SH,000001.SZ&show=true`

添加到自选股 / 自定义板块;`stocks=` 空值 = 清空;`block_code=ZXG` 是"自选股"。TQ 内部会自己把代码改写成 `'1#688318|0#002475'` 格式,**不要预先格式化**。

### 板块 CRUD

```bash
# 创建
curl -X POST "http://localhost:8080/api/v1/notify/sector?block_code=PROJECTION&block_name=我的预测"

# 改名(只能改名称,简称不能改)
curl -X PUT "http://localhost:8080/api/v1/notify/sector/PROJECTION?block_name=预测v2"

# 清空成分股
curl -X POST "http://localhost:8080/api/v1/notify/sector/PROJECTION/clear"

# 删除
curl -X DELETE "http://localhost:8080/api/v1/notify/sector/PROJECTION"
```

---

## 管理 / 监控(运维用)

> 这些通常不在业务路径里;`/admin/*` 也受 API Key 鉴权。

| 端点 | 用途 |
|------|------|
| `GET /api/v1/admin/status` | 进程 PID / 内存 / uptime + TQ 探活 + 鉴权状态 |
| `GET /api/v1/admin/metrics?reset=true&hot_threshold=20` | 调用统计(按 route template 聚合 p50/p95/p99)+ 优化建议 |
| `GET /api/v1/admin/config` | 当前配置(API key 仅返掩码) |
| `GET /api/v1/admin/logs/list` `/api/v1/admin/logs?lines=200` | 日志列表 / 末尾 N 行 |
| `POST /api/v1/admin/tq/test` | 探活 TQ(实际调一次 `get_stock_list`) |
| `POST /api/v1/admin/restart?delay_seconds=2` | **重启服务**(写触发器 → 杀 PID → 起新进程) |
| `POST /api/v1/admin/cache/refresh?market=AG&force=true` | 强制刷新某市场缓存 |
| `POST /api/v1/admin/kline/refresh?stocks=600519.SH&period=1d` | 刷新单股 K 线缓存 |
| `POST /api/v1/admin/cache/invalidate` | 清空本进程内存缓存 |
| `POST /api/v1/admin/tq/close` | 断开 TQ 连接(下次请求自动重连) |
| `POST /api/v1/admin/download` | 下载文件(待实现) |

---

## 错误码

| HTTP | 含义 | 触发场景 | 处理建议 |
|------|------|---------|---------|
| `200` | 成功 | — | — |
| `400` | 用户输入错 | 字段名拼错 / 路径不带 `.SH` / 空 codes / 非白名单 period / 非法 block_type | 检查请求参数;`detail` 里有提示 |
| `401` | 鉴权失败 | 配了 `TDX_API_KEY` 但没带 `X-API-Key` 或带错 | 加上正确的 key 头 |
| `404` | 路由不存在 | URL 写错 | 检查 `/openapi.json` |
| `422` | 请求体 / 查询参数解析失败 | `?start=not-a-date` 等 | 看 `detail[].msg` |
| `503` | 通达信客户端不可用 | TdxW.exe 没起 / 没登录 / DLL 连接被占用 | 重启 TdxW 或 `POST /admin/tq/close` 重连 |
| `5xx` | 服务端 bug | 看 `logs/background-*.log` 或 `/admin/logs` | 提 issue |

`400` 响应体里带 `bad_request: true`,`503` 带 `tq_reachable: false`,方便客户端判断。

---

## 限流与缓存建议

1. **TQ DLL 是单连接单线程**。服务端所有 TQ 调用串行排队 —— 你的并发请求不会让 DLL 崩,只是慢。
2. **一次拉太多股会超时**。K 线单次 ≤ 600 支,推荐 250 支一批(`../qtTdx/backtrace/data_fetch/fetch_daily.py` 就是这个 BATCH_SIZE)。
3. **股票 / 板块 / 交易日列表有缓存**(默认 1 小时)。需要最新数据时带 `no_cache=true`。
4. **K 线无缓存** —— 每次都真调 TQ,所以频繁拉同一段历史会浪费配额。
5. **Cloudflare 隧道有免费额度**,日请求数 < 100k 一般安全。

---

## Python 客户端示例

直接 `requests` 调:

```python
import pandas as pd
import requests

BASE = "https://redmitdx.gptalk.us.kg"  # 本机: "http://localhost:8080"
API_KEY = "your-secret"  # 没配就传 None

def get(path: str, **params):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    r = requests.get(f"{BASE}{path}", params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


# 1. 沪深 300 成分股
items = get("/api/v1/stocks", market="23")["items"]
hs300 = [x["Code"] for x in items]
print(f"沪深 300 共 {len(hs300)} 只")

# 2. 拉日线前复权
data = get("/api/v1/kline", codes=",".join(hs300[:50]),
           period="1d", start="20250101", end="20260101",
           dividend="front", fields="Open,Close,Volume")
close = pd.DataFrame(data["data"]["Close"]["records"],
                     index=pd.to_datetime(data["data"]["Close"]["index"]))
print(close.tail())

# 3. 财务:近 4 个季度的 ROE + 营收
fin = get("/api/v1/finance/600519.SH",
          fields="FN197,FN230", start="20240101", end="20260822")
print(fin["data"])

# 4. 板块成分
stocks = get("/api/v1/sectors/881002.SH/stocks")["items"]
print(f"煤炭板块 {len(stocks)} 只: {stocks[:5]}")

# 5. 实时快照批量
snap = get("/api/v1/kline/snapshot", codes="600519.SH,000001.SZ")
print(snap["snapshots"])
```

更稳的封装(带错误重试 / 缓存):

```python
import time
import requests

class TdxDataClient:
    def __init__(self, base="http://localhost:8080", api_key=None,
                 retry_503=True, max_retries=3):
        self.base = base.rstrip("/")
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key
        self.retry_503 = retry_503
        self.max_retries = max_retries

    def get(self, path, **params):
        last_err = None
        for i in range(self.max_retries):
            try:
                r = self.session.get(f"{self.base}{path}", params=params, timeout=30)
                if r.status_code == 503 and self.retry_503:
                    # TQ 不可用 — 等 2s 重试,给 TdxW.exe 重连的时间
                    time.sleep(2)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                time.sleep(1)
        raise last_err

    # 业务快捷方法
    def a_share_codes(self, market="5"):
        return [x["Code"] for x in self.get("/api/v1/stocks", market=market)["items"]]

    def kline(self, codes, period="1d", start=None, end=None,
              count=None, dividend="none", fields=None):
        return self.get("/api/v1/kline", codes=",".join(codes), period=period,
                        start=start, end=end, count=count, dividend=dividend,
                        fields=fields)

    def sector_stocks(self, code, block_type=0):
        return self.get(f"/api/v1/sectors/{code}/stocks", block_type=block_type)["items"]


# 用法
cli = TdxDataClient(base="https://redmitdx.gptalk.us.kg", api_key="xxx")
hs300 = cli.a_share_codes("23")
kline = cli.kline(hs300[:100], period="1d",
                  start="20250101", end="20260101", dividend="front")
```

---

## 常见坑(快速对照)

| 现象 | 原因 | 处理 |
|------|------|------|
| 503 + `tq_reachable=false` | 通达信 TdxW.exe 没起或没登录 | 起客户端、登录账号;`/health/ready` 探活 |
| `/kline` 返 `data: {}` | `codes` 拼错 / 标的没行情 | 检查 `codes`,本机客户端里有没有这只股 |
| `/finance` 返空表 | 没传 `fields` | 加 `fields=FN1,FN197...` |
| `/sectors/881002/stocks` 返 0 | 申万代码没带 `.SH` | 改成 `881002.SH` |
| `/sectors?list_type=11` 返 0 | TQ 只支持 `list_type=0/1` | 改 `0` 或 `1` |
| 中文显示 `\uXXXX` | 客户端没用 UTF-8 解码 | 服务端返回的是 UTF-8,用 `response.json()` 或 `Content-Type: charset=utf-8` |
| `req.get(...)` 超时 | 单次拉了 > 600 支股票 | 分批,250 一组 |
| `/admin/*` 401 | 没带 API Key | 加 `X-API-Key` 头 |
| `forward_factor` 之类内部字段出现 | 不应出现(被服务端过滤) | 如果出现 → 服务端有 bug,提 issue |

---

## 进一步

- Swagger UI:`/docs`
- OpenAPI JSON:`/openapi.json`
- 服务端源码 + 字段定义:`<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html>`(Fn)
- 内部运维文档:`../CLAUDE.md`
- 字段代码官方文档:
  - Fn (基础/专业财务):<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html>
  - GP (股票交易):<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10muc82r55k.html>
  - BK (板块交易):<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10p0ncmp5mc.html>
  - SC (市场):<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10p8op6ia9g.html>
  - GO (股票单数据):<https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10pk3rsg044.html>