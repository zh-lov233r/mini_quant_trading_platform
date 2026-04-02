# quant-trading-system

## Docker

这个仓库现在可以直接用 Docker Compose 跑起完整的本地环境：

- `frontend`: Next.js，默认暴露在 `http://localhost:3000`
- `backend`: FastAPI，默认暴露在 `http://localhost:8000`
- `db`: PostgreSQL 16，默认暴露在 `localhost:5432`

### 1. 准备环境变量

把示例文件复制成你自己的 Docker 配置：

```bash
cp .env.docker.example .env.docker
```

这样不会覆盖你仓库里原本的 `.env`。`docker compose` 和本地 Python/Node 开发配置可以分开维护。

如果你需要自定义端口或数据库账号，至少确认 `.env.docker` 里这些变量存在或接受默认值：

```env
POSTGRES_DB=quant
POSTGRES_USER=quant
POSTGRES_PASSWORD=quantpass
POSTGRES_PORT=5432
FRONTEND_ORIGIN=http://localhost:3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

如果你需要跑 Massive / Alpaca 相关功能，再额外补这些：

```env
MASSIVE_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

### 2. 启动

```bash
make docker-up
```

或者直接：

```bash
docker compose --env-file .env.docker up --build -d
```

### 3. 查看日志

```bash
make docker-logs
```

### 4. 停止

```bash
make docker-down
```

## 说明

- backend 启动时会先执行 `backend/utils/create_db.py`，自动创建项目需要的表。
- Compose 会把本地 `./data` 挂载到容器里的 `/app/data`，把 `./logs` 挂载到 `/app/logs`。
- `NEXT_PUBLIC_API_BASE_URL` 是前端构建时变量；改完后需要重新 build frontend 镜像。






