# HardSeat Hero

根据出发地、目的地和出行日期，查询低价火车直达与中转方案的 Web 应用原型。

## 技术栈

- 前端：React、TypeScript、Vite、Tailwind CSS、本地 shadcn/ui 风格组件。
- 后端：FastAPI、Pydantic v2、httpx、pytest、SQLite。
- 数据源：通过 `TrainDataProvider` 统一适配 Mock、12306 公布票价接口和 SQLite 静态票价库。
- 默认数据源为 `mock`；可通过 `TRAIN_DATA_PROVIDER=12306-public-price` 试用 12306 公布票价接口，或通过 `TRAIN_DATA_PROVIDER=static-price` 使用本地静态票价库。

## 目录

```text
backend/   FastAPI 服务、领域模型、数据源适配器、中转搜索、缓存、遥测和脚本
frontend/  React 查询页面和结果列表
docs/      需求、设计、实施计划、数据源调研和整体架构文档
data/      种子 OD CSV；运行时 SQLite 数据库默认不应提交
```

当前完整架构说明见 `docs/项目整体架构.md`。

## 后端启动

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

默认使用 Mock 数据源。如需试用 12306 公布票价接口：

```powershell
$env:TRAIN_DATA_PROVIDER = "12306-public-price"
uvicorn app.main:app --reload --port 8000
```

该接口不是公开稳定 API，失败时 API 会返回受控错误，Mock 数据源不受影响。

### 可选缓存和遥测

```powershell
$env:ROUTE_SEGMENT_CACHE_DB = "D:\AbelTomato_Files\Developer\Projects\HardSeat-Hero\data\route_segment_cache.sqlite3"
$env:ROUTE_SEGMENT_CACHE_TTL_SECONDS = "3600"
$env:SEARCH_TELEMETRY_DB = "D:\AbelTomato_Files\Developer\Projects\HardSeat-Hero\data\search_telemetry.sqlite3"
```

这些 SQLite 文件属于本地运行时数据，已在 `.gitignore` 中忽略。

### 静态票价库模式

如果已经通过 `backend/scripts/refresh_static_prices.py` 刷新过本地静态票价库，可以使用 `static-price` provider 查询 SQLite 本地数据。

推荐使用 `STATIC_PRICE_DB` 的**绝对路径**，避免从项目根目录和 `backend/` 目录启动时解析到不同数据库文件。

只读本地静态库：

```powershell
$env:TRAIN_DATA_PROVIDER = "static-price"
$env:STATIC_PRICE_DB = "D:\AbelTomato_Files\Developer\Projects\HardSeat-Hero\data\static_prices.sqlite3"
$env:STATIC_PRICE_MODE = "static-only"
$env:STATIC_PRICE_MAX_AGE_DAYS = "30"
uvicorn app.main:app --reload --port 8000
```

本地未命中或过期时远程补洞并写回静态库：

```powershell
$env:TRAIN_DATA_PROVIDER = "static-price"
$env:STATIC_PRICE_DB = "D:\AbelTomato_Files\Developer\Projects\HardSeat-Hero\data\static_prices.sqlite3"
$env:STATIC_PRICE_MODE = "static-with-remote-fallback"
$env:STATIC_PRICE_FALLBACK_PROVIDER = "12306-public-price"
$env:STATIC_PRICE_MAX_AGE_DAYS = "30"
uvicorn app.main:app --reload --port 8000
```

注意：`static-with-remote-fallback` 会在查询缺失或过期 OD 时访问 12306 公布票价接口，可能变慢、失败或被限流。该模式只保证公布票价查询，不保证实时有票。

手动刷新静态库示例：

```powershell
cd backend
python scripts/refresh_static_prices.py --date 2026-07-01 --od-file ../data/seed_od.csv --db ../data/static_prices.sqlite3 --interval-seconds 1
```

查看静态库整体覆盖：

```powershell
cd backend
python scripts/inspect_static_prices.py --db ../data/static_prices.sqlite3
```

查看指定 OD 静态票价明细：

```powershell
cd backend
python scripts/inspect_static_prices.py --db ../data/static_prices.sqlite3 --date 2026-07-01 --from 北京 --to 上海
```

如果已启用 `SEARCH_TELEMETRY_DB`，可从搜索遥测导出下一批待刷新 OD：

```powershell
cd backend
python scripts/export_refresh_od_from_telemetry.py --telemetry-db ../data/search_telemetry.sqlite3 --output ../data/refresh_od.csv
```

健康检查：`http://localhost:8000/api/health`

方案查询：`POST http://localhost:8000/api/routes/search`

## 前端启动

```powershell
cd frontend
pnpm install
pnpm dev
```

默认连接 `http://localhost:8000`。如需修改后端地址，设置 `VITE_API_BASE_URL`。

## 测试

```powershell
cd backend
pytest
```

```powershell
cd frontend
pnpm build
```

本项目前端使用 pnpm 管理依赖。

## 本地联调

分别启动后端和前端：

```powershell
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000 --app-dir backend
```

```powershell
pnpm --dir frontend dev
```

- 前端页面：`http://localhost:5173/`
- 后端 API：`http://127.0.0.1:8000/api`
- 当前 Mock 数据中，北京到上海的最低价中转方案为经 `南京南`，总价 `413.0`。
- 出发地和目的地输入框会调用 `GET /api/stations/search` 提供站点自动补全。
