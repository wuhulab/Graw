# Contributing — 贡献指南

感谢你对 Graw 的关注与贡献！本指南帮助你快速上手本地开发、了解代码约定并完成一次合规的 Pull Request。

仓库主页：<https://github.com/wuhulab/Graw>

## 1. 本地开发环境

### 环境要求
- Go 1.23+
- Node.js 16+
- （可选）Docker 引擎 —— 仅 Docker 管理相关功能需要

### 启动方式（前后端分离）
```bash
# 后端（Go；Windows 使用 start.bat，Linux/macOS 使用 start.sh）
cd backend-go
go mod download
go run .       # 默认监听 :8000

# 前端（另开终端）
cd frontend
npm install
npm run dev    # → http://localhost:5173，Vite 代理 /api 与 ws 到 :8000
```

> `backend/` 为 Python/FastAPI 旧版后端（保留兼容与回归测试）。如需运行旧版：`cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`。

> 注意：Windows 的 PowerShell/cmd 不支持 `&&`，多命令请用分号 `;` 分隔。

## 2. 代码结构速览

AI 改动代码前请通读根目录 `AGENTS.md`，它描述了整体架构、权限模型与常见陷阱。关键约定摘录：

| 想做什么 | 去哪里 |
|---------|--------|
| 新增一个 REST 业务模块 | Go 默认：`backend-go/internal/graw/handlers_xxx.go` + `httpx.go` 注册；Python 旧版：`backend/app/routers/xxx.py` + `main.py` 的 `include_router` |
| 新增一个前端功能页 | `frontend/src/components/windows/XxxWindow.vue`，并在 `App.vue` 的 shortcuts 注册入口（桌面/标准面板两种模式共用） |
| 登录 / 鉴权 / 用户 | Go：`backend-go/internal/graw/{auth,sessions,handlers_auth}.go`；Python 旧版：`backend/app/auth.py`、`routers/auth.py` |
| 多节点 / 子节点 Agent | Go：`backend-go/internal/graw/agent.go`；Python 旧版：`agent_auth.py`、`agent_cfg.py`、`agent_client.py`、`node_manager.py`（实操见 `docs/node-agent.md`） |
| 实时监控 / 指标采集 | Go：`backend-go/internal/graw/{sysmon,metrics_store,handlers_system}.go`、`frontend/src/store/systemMetrics.js` |
| 应用商店配方 | `app-store/`（YAML），面板侧机制见 `docs/app-store-recipe.md` |
| 改文档 | 开发者/运维文档在 `docs/`，运行逻辑在 `GETMEREAD/`（更新规矩见其 README），文档总索引 `docs/README.md` |

### 必须遵守的约束
- **不要引入 Pinia**：前端全局状态使用 `store/*.js` 的 `reactive` 单例模式。
- **数据持久化**：配置/凭据以 JSON 文件存于 `backend/data/`，写入用「临时文件 + 原子替换」（Go `os.Rename` / Python `os.replace()`）；不要放宽该目录权限、不要明文回传凭据。
- **接口鉴权分级**：只读类仅需登录，写操作/命令执行要求管理员或对应模块权限；WebSocket 与部分端点（`/api/ui` public、ShunX）在端点内自行鉴权。
- **CORS 默认关闭**：同源部署，不要为联调方便放开 `*`。
- **多节点透传**：新增接口若需对子节点透传，确认它不在 Agent 代理排除前缀内；涉及本地宿主机能力时做远端能力门控。

## 3. 测试

Go 后端测试位于 `backend-go/`（`*_test.go`）：

```bash
cd backend-go
go vet ./...
go test ./... -count=1
```

Python 旧版回归测试位于 `backend/`，命名遵循 `test_<模块>_(unit|e2e).py`：

```bash
cd backend
pip install -r requirements.txt
python -m pytest test_*_unit.py -q
python -m pytest test_2fa_unit.py test_auditlog_unit.py -q   # 指定模块
```

- 新增/修改功能时请补充对应测试（两套后端已有大量用例可参考）。
- 修改后端后保证 Go 编译与 `go vet` 通过（Python 旧版无语法错误）；修改前端后保证 `npm run build` 通过。

## 4. 代码风格

- **后端**：Go 默认后端保持标准库风格、错误显式返回，注释用中文描述背景与安全约束；Python 旧版使用类型注解 + Pydantic 模型、`logging.getLogger("graw.xxx")`。敏感操作前做鉴权断言。
- **前端**：`<script setup>` Composition API；组件名 PascalCase；样式优先 scoped；新增界面文案走 `src/locales/` 的 i18n key，不要硬编码中文。
- 编辑器统一风格见根目录 `.editorconfig`。
- 不要提交 `backend/data/` 下任何运行时文件（已 gitignore）。

## 5. 提交与 Pull Request

### Commit 消息
建议使用 Conventional Commits 风格（与现有历史风格兼容）：

```
<type>: <描述>

type: feat / fix / docs / style / refactor / test / chore / build
```

示例：`fix: 修复实时监控数据闪断`、`feat: 新增回收站自动清理`、`docs: 补充贡献指南`。

### 流程
1. Fork 仓库并创建功能分支：`git checkout -b feat/xxx`。
2. 小步提交，逻辑独立；不要混入无关改动。
3. 推送并创建 Pull Request，PR 描述请使用模板（`.github/PULL_REQUEST_TEMPLATE.md`）。
4. 保持 PR 聚焦：一次 PR 解决一个问题，方便 review 与回滚。

### Review 前自查
- [ ] 已阅读并同意第 7 节《贡献者许可协议（CLA）》
- [ ] 已通读 `AGENTS.md`，改动符合鉴权/持久化/多节点约定
- [ ] Go 后端编译与 `go vet` 通过，新增/修改模块已注册路由（Python 旧版无语法错误）
- [ ] 前端 `npm run build` 通过，界面文案走 i18n
- [ ] 关键逻辑有对应测试用例
- [ ] 未提交敏感信息（`data/`、`.env`、密钥）

## 6. 安全相关

发现安全问题请**不要**在公开 Issue 中张贴，按 [SECURITY.md](./SECURITY.md) 的流程报告。

## 7. 贡献者许可协议（CLA）

> 本节即 Graw 项目的贡献者许可协议（Contributor License Agreement，简称 CLA）。

**提交即视为同意**：你以任何形式向本项目提交代码、文档、配置或其他内容（通过 Pull Request、补丁、Issue 附件等，以下统称「贡献」），即表示你已阅读、理解并**默认同意**本协议的全部条款。**若你不同意，请不要提交贡献**。

1. **著作权归属**：你保留对所提交贡献的著作权；本协议不转让著作权，仅授予下述许可。
2. **版权许可**：你授予 **WuHuLaB**（及其关联方、继承方、受让方）一项**永久、全球范围、不可撤销、免费、非独占**的版权许可，允许其复制、修改、再许可、分发与以任何方式使用你的贡献。
3. **保留商业闭源权利**：你明确同意，WuHuLaB 有权将包含你贡献的代码用于**商业用途**，并有权以**闭源（专有）许可**再许可或分发（包括但不限于商业版、企业版、云托管/订阅服务），**无需另行通知你，也无需向你支付任何费用**。开源仓库中的代码仍按 [AGPLv3](./LICENSE) 向公众提供。
4. **专利许可**：你授予 WuHuLaB 及其用户一项永久、全球范围、不可撤销、免费的专利许可，允许其使用你的贡献中必然被侵犯的专利权利要求。
5. **原创性保证**：你保证贡献为本人原创，或已取得足以按本协议授予上述全部许可的权利；贡献不侵犯任何第三方权利，且不含违反法律法规或第三方许可的内容。
6. **无需单独签署**：本项目采用「默认同意」模式，**无需**另行签署纸质或电子版 CLA 文件；提交 Pull Request 即等同于签署本协议。

## 8. 提问与讨论

- Bug 反馈 / 功能建议：<https://github.com/wuhulab/Graw/issues>
- 一般问题请在提交前先搜索是否存在类似 Issue，避免重复。

再次感谢你的贡献！