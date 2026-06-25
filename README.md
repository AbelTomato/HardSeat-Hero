# HardSeat Hero

根据出发地、目的地和出行日期，查询低价火车直达与中转方案的 Web 应用原型。

## 技术栈

- 前端：React、TypeScript、Vite、Tailwind CSS、shadcn/ui 风格组件。
- 后端：FastAPI、Pydantic、pytest。
- 数据源：当前使用 Mock 数据源，后续通过 `TrainDataProvider` 接入 GitHub 开源项目或 MCP 查询工具。
- 真实数据源：可通过 `TRAIN_DATA_PROVIDER=12306-public-price` 试用 12306 公布票价接口适配器；默认仍为 `mock`。

## 目录

```text
backend/   FastAPI 服务、领域模型、Mock 数据源、中转搜索算法
frontend/  React 查询页面和结果列表
docs/      需求、设计、实施计划和数据源调研
```

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
