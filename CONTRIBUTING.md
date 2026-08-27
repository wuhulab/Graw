# 贡献指南（CONTRIBUTING）

感谢你对 Graw 的关注！Graw 是一个采用「类桌面操作系统」交互设计的开源服务器管理面板，
由 ShunX 公益团队发起维护。本指南帮助新贡献者快速上手。

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 中的行为准则。

---

## 1. 开始之前

- 阅读根目录 [README.md](README.md) 了解功能与快速开始。
- 阅读 [AGENTS.md](AGENTS.md) —— 它记录了架构约定与已知陷阱，**改代码前请先通读**。
- 确认你遵循的许可证与商用条款：项目以 AGPLv3 为基础的定制开源许可
  （见 [LICENSE](LICENSE) 与 [license.txt](license.txt)）。

## 2. 开发环境

### 环境要求

- Python 3.11（后端，生产镜像版本；本地 3.8+ 可运行）
- Node.js 16+（前端）
-（可选）Docker 引擎（Docker 管理功能需要）

### 本地启动（前后端分离）

```bash
# 后端
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端（另开终端）
cd frontend
npm install
npm run dev     # → http://localhost:5173（Vite 代理 /api 与 WebSocket 到 :8000）
```

或使用一键脚本：`start.sh`（Linux/macOS）/ `start.bat`（Windows）。

> 注意：请在 Windows PowerShell 中不要使用 `&&`，需用 `;` 分隔命令。

## 3. 代码约定

### 后端（FastAPI）

- 新增 REST 业务模块：新建 `backend/app/routers/xxx.py`，并在
  `backend/app/main.py` 用 `app.include_router(..., prefix="/api/xxx")` 注册。
- 鉴权分级：只读信息类用 `PROTECTED`，写操作/命令执行类用 `ADMIN`；
  WebSocket / 部分端点（`/api/ui` public、ShunX、VIP、terminal）在端点内部自行鉴权，
  不要误挂全局依赖。
- 数据持久化：配置/凭据以 JSON 存于 `backend/data/`，读写时优先「原子写」
  （临时文件 + `os.replace()`），不要放宽 `data/` 目录权限。
- 多节点 / Agent：明确新接口是 local-only（挂 `remote_cap`）还是需要子节点透传，
  不要绕过 Agent 代理中间件。
- 常量驻后台协程请在 `main.py` 的 `lifespan()` 中**配对启动/停止**。
- 风格：类型注解 + Pydantic 模型；docstring 用中文说明背景与安全约束；
  日志用 `logging.getLogger("graw.xxx")`。

### 前端（Vue 3）

- 使用 `<script setup>` Composition API；组件 PascalCase；样式优先 scoped。
- 状态管理使用自定义 `reactive` 单例（`src/store/*.js`），**不要引入 Pinia**。
- 新增功能页：新建 `src/components/windows/XxxWindow.vue` 并在桌面注册入口。
- 国际化：界面文案走 `vue-i18n`（`src/locales/`），不要硬编码中文。
- 所有 API 调用统一经 `src/api.js` 注入 `Authorization: Bearer <token>`。

## 4. 测试

- 后端测试位于 `backend/test_*.py`：
  - pytest 风格（如 `test_security_regression.py`、`test_frp_configpath_regression.py`）：
    `cd backend && python -m pytest test_security_regression.py -v`
  - 自执行脚本风格（自带 PASS/FAIL 计数）：
    `cd backend && python test_firewall_unit.py`
- 提交前请在本地把改动涉及的测试跑一遍；CI 会在 PR 中运行全部健康测试集。

## 5. 提交与 PR 流程

1. 从 `main` 创建功能分支，命名建议：`feat/xxx`、`fix/xxx`、`docs/xxx`。
2. 小步提交，提交信息用中文或英文均可，格式建议：
   - `fix: 修复实时监控数据闪断`
   - `feat: 新增会话管理`
   - `docs: 更新 README`
3. 推送分支后开 Pull Request，填写 `.github/PULL_REQUEST_TEMPLATE.md` 中的内容：
   - 关联的 issue 编号（如有）
   - 变更内容与动机
   - 如何验证（测试命令、截图等）
4. 变更涉及「子节点 Agent」功能时，请说明是否需要同步子节点代码，并在 PR 描述中注明。

## 6. 发布流程（维护者）

- 版本号单一来源：`backend/app/main.py` 的 `APP_VERSION`。
- 打 tag（如 `v1.5.2`）将自动触发 Docker Hub 镜像构建与发布
  （见 `.github/workflows/docker-publish.yml`）。
- 发版前更新 [CHANGELOG.md](CHANGELOG.md)。

## 7. 问题与讨论

- Bug / 功能建议：使用 GitHub Issues（模板见 `.github/ISSUE_TEMPLATE/`）。
- 安全问题：请**不要**在公开 issue 中披露，按 [SECURITY.md](SECURITY.md) 私有报告。