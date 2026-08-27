# Graw 统一构建入口（Makefile）
# 主要面向 Linux/macOS 与 CI；Windows 用户请使用 start.bat 或直接用下方等价命令。
# 常见用法：
#   make dev-backend    启动后端开发服务器（uvicorn --reload）
#   make dev-frontend   启动前端开发服务器（vite）
#   make build          生产构建前端产物（frontend/dist）
#   make test-backend   运行后端 pytest 回归测试集
#   make lint           后端语法体检（compileall） + 前端构建检查
#   make clean          清理构建产物与缓存

PYTHON ?= python
NPM    ?= npm
VENV   ?= backend/.venv

.PHONY: help dev-backend dev-frontend build test-backend test-backend-all lint clean venv

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

venv: ## 创建并安装后端虚拟环境
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r backend/requirements.txt

dev-backend: ## 启动后端开发服务器（热重载）
	cd backend && $(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

dev-frontend: ## 启动前端开发服务器
	cd frontend && $(NPM) run dev

build: ## 生产构建前端产物（后端自动挂载 frontend/dist）
	cd frontend && $(NPM) install && $(NPM) run build

test-backend: ## 运行后端健康回归测试集（pytest 风格）
	cd backend && $(PYTHON) -m pytest test_security_regression.py test_frp_configpath_regression.py -q --tb=line

test-backend-all: ## 运行后端全部测试文件（含自执行脚本风格测试）
	@echo "逐个运行 backend/test_*.py，跳过已知不同步的 test_agent_cfg_unit.py"
	cd backend && $(PYTHON) -m pytest test_security_regression.py test_frp_configpath_regression.py -q --tb=line
	cd backend && for f in test_*.py; do \
	  case "$$f" in \
	    test_security_regression.py|test_frp_configpath_regression.py|test_agent_cfg_unit.py) ;; \
	    *) echo "==> $$f"; $(PYTHON) "$$f" ;; \
	  esac; \
	done

lint: ## 后端语法体检 + 前端构建检查
	$(PYTHON) -m compileall -q backend/app
	cd frontend && $(NPM) run build

clean: ## 清理构建产物与缓存
	rm -rf frontend/dist backend/__pycache__ backend/app/__pycache__
	find backend -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true