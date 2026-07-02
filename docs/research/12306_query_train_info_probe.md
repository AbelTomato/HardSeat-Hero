# 12306 queryTrainInfo 入口验证摘要

验证时间：2026-06-30 17:04-17:10，目标入口：`https://kyfw.12306.cn/otn/queryTrainInfo/init`。

## 结论

- `queryTrainInfo/init` 本身只是时刻表查询页面入口，不直接返回当天全部车次数据。
- 页面 JS 暴露的关键接口包括：
  - `https://search.12306.cn/search/v1/train/search?keyword=...&date=yyyyMMdd`：按关键字搜索车次，空 keyword 返回空数组；不适合作为一次性全量入口。
  - `/otn/queryTrainInfo/getTrainName?date=yyyy-MM-dd`：返回指定日期可查询车次列表，本次响应约 1.18MB，原始记录 16263 条，按 `train_no` 去重 11847 条。
  - `/otn/queryTrainInfo/query?leftTicketDTO.train_no=...&leftTicketDTO.train_date=yyyy-MM-dd&rand_code=`：按单个 `train_no` 返回该车停站时刻表。
- 因此：作为“当天全部车次获取途径”，可行路径不是 `init`，而是先调用 `getTrainName(date)` 获取当天车次清单，再逐个调用 `query` 获取停站明细。
- 直接通过 `search/v1/train/search` 穷举字母/数字可以补充搜索，但它按关键字限制，且示例 `keyword=G` 只返回 200 条，不是可靠全量入口。

## 已验证请求

- 入口页：HTTP 200，`init.html` 29,244 bytes。
- 车次搜索：`keyword=G&date=20260630`，HTTP 200，返回 200 条；`keyword=` 返回空数组。
- 单车时刻：G1 / `24000000G10L` / `2026-06-30`，HTTP 200，返回 7 个停靠站。
- 当天车次清单：`getTrainName?date=2026-06-30`，HTTP 200，响应 1,238,399 bytes。

## 样例字段

`getTrainName` 单项字段：`station_train_code`、`train_no`。

`query` 单站字段示例：`station_name`、`station_train_code`、`start_time`、`arrive_time`、`station_no`、`running_time`、`start_station_name`、`end_station_name`。

## 文件

- `init.html` / `init.headers.txt` / `cookies.txt`
- `assets/queryTrainInfo_js.js` / `assets/route.js` / `assets/focused_clues.txt`
- `query_attempts/train_search_G.json` / `train_search_empty.json`
- `query_attempts/getTrainName.json`
- `query_attempts/query_G1.json`

## 风险与限制

- `getTrainName` 响应含重复 `train_no`，需要去重。
- 接口属于公网 12306 页面接口，可能变更、限流或要求验证码/cookie；应做缓存、失败重试、限速和字段兼容。
- 若项目需要“余票/席别可售”，该入口不够；它主要提供车次清单和停站时刻，不等价于余票查询接口。