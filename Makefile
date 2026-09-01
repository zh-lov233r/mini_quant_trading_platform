SHELL := /bin/zsh

ROOT_DIR := $(CURDIR)
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
PYTHON := $(ROOT_DIR)/.venv/bin/python
SUPERVISE_BACKTEST_MANAGER = while true; do (cd "$(BACKEND_DIR)" && PAPER_TRADING_SCHEDULER_ENABLED=false PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false "$(PYTHON)" -m src.workers.backtest_worker_manager $(BACKTEST_WORKER_MANAGER_ARGS)); exit_code=$$?; echo "Backtest worker manager exited (code $$exit_code); restarting in 2 seconds." >&2; sleep 2; done

.PHONY: help dev dev-agent-all dev-agent-safe dev-backend dev-frontend backtest-worker backtest-worker-manager backfill-daily check-data docker-build docker-up docker-down docker-logs

help:
	@echo "Available targets:"
	@echo "  make dev          Start backend, frontend, and manager (BACKTEST_WORKER_CONCURRENCY=1|2, default 2)"
	@echo "  make dev-agent-all Start AgentOps DB/API and Quant backend/frontend safely"
	@echo "  make dev-agent-safe Start Quant for AgentOps with all paper order automation disabled"
	@echo "  make dev-backend  Start FastAPI backend only (partial stack; no automatic backtest worker)"
	@echo "  make dev-frontend Start Next.js frontend only (partial stack)"
	@echo "  make backtest-worker Run the independent durable backtest worker"
	@echo "  make backtest-worker-manager Run and supervise the on-demand manager (inherits BACKTEST_WORKER_CONCURRENCY)"
	@echo "  make backfill-daily Run the daily market-data catch-up flow"
	@echo "  make check-data     Run read-only market-data integrity checks"
	@echo "  make docker-build Build all Docker images"
	@echo "  make docker-up    Start the full Docker stack in background"
	@echo "  make docker-down  Stop the Docker stack"
	@echo "  make docker-logs  Tail Docker Compose logs"

dev:
	@trap 'kill 0' INT TERM EXIT; \
		$(SUPERVISE_BACKTEST_MANAGER) & \
		cd "$(BACKEND_DIR)" && PAPER_TRADING_SCHEDULER_ENABLED=false PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false "$(PYTHON)" -m uvicorn src.main:app --reload --port 8000 & \
		cd "$(FRONTEND_DIR)" && npm run dev & \
		wait

dev-agent-all:
	@"$(PYTHON)" scripts/dev_agent_stack.py

dev-agent-safe:
	@test -n "$(QUANT_AGENT_SERVICE_TOKEN)" || { echo "QUANT_AGENT_SERVICE_TOKEN is required"; exit 1; }
	@test -n "$(AGENTOPS_PROJECT_ID)" || { echo "AGENTOPS_PROJECT_ID is required"; exit 1; }
	@trap 'kill 0' INT TERM EXIT; \
		$(SUPERVISE_BACKTEST_MANAGER) & \
		cd "$(BACKEND_DIR)" && QUANT_AGENT_INTEGRATION_ENABLED=true QUANT_AGENT_SERVICE_TOKEN="$(QUANT_AGENT_SERVICE_TOKEN)" RESEARCH_WORKER_ENABLED=true PAPER_TRADING_SCHEDULER_ENABLED=false PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false "$(PYTHON)" -m uvicorn src.main:app --reload --port 8000 & \
		cd "$(FRONTEND_DIR)" && NEXT_PUBLIC_AGENTOPS_API_BASE_URL=http://localhost:8100 NEXT_PUBLIC_AGENTOPS_PROJECT_ID="$(AGENTOPS_PROJECT_ID)" npm run dev & \
		wait

dev-backend:
	@cd "$(BACKEND_DIR)" && "$(PYTHON)" -m uvicorn src.main:app --reload --port 8000

dev-frontend:
	@cd "$(FRONTEND_DIR)" && npm run dev

backtest-worker:
	@cd "$(BACKEND_DIR)" && PAPER_TRADING_SCHEDULER_ENABLED=false PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS=false "$(PYTHON)" -m src.workers.backtest_worker $(BACKTEST_WORKER_ARGS)

backtest-worker-manager:
	@$(SUPERVISE_BACKTEST_MANAGER)

backfill-daily:
	@cd "$(ROOT_DIR)" && "$(PYTHON)" backend/utils/run_daily_market_backfill.py $(BACKFILL_ARGS)

check-data:
	@cd "$(ROOT_DIR)" && "$(PYTHON)" backend/utils/check_market_data_quality.py $(CHECK_DATA_ARGS)

docker-build:
	@docker compose --env-file .env.docker build

docker-up:
	@docker compose --env-file .env.docker up --build -d

docker-down:
	@docker compose --env-file .env.docker down

docker-logs:
	@docker compose --env-file .env.docker logs -f --tail=100
