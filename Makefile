SHELL := /bin/zsh

ROOT_DIR := $(CURDIR)
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
PYTHON := $(ROOT_DIR)/.venv/bin/python

.PHONY: help dev dev-backend dev-frontend

help:
	@echo "Available targets:"
	@echo "  make dev          Start backend and frontend together"
	@echo "  make dev-backend  Start FastAPI backend only"
	@echo "  make dev-frontend Start Next.js frontend only"

dev:
	@trap 'kill 0' INT TERM EXIT; \
		cd "$(BACKEND_DIR)" && "$(PYTHON)" -m uvicorn src.main:app --reload --port 8000 & \
		cd "$(FRONTEND_DIR)" && npm run dev & \
		wait

dev-backend:
	@cd "$(BACKEND_DIR)" && "$(PYTHON)" -m uvicorn src.main:app --reload --port 8000

dev-frontend:
	@cd "$(FRONTEND_DIR)" && npm run dev
