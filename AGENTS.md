# agent.md — Graw 项目指引

本文件为 AI 编码助手（以及新加入的人类开发者）提供 Graw 代码库的结构、约定与常见陷阱速查。
改动代码前请先通读本文件，确保改动符合既有架构与安全约束。

你可以读取项目skills：agent文件夹下

---

## 1. 项目简介

**Graw** 是一个基于 Web 的服务器管理面板，采用「类桌面操作系统」的交互设计（窗口、任务栏、桌面快捷方式）。
它不仅能管理本机，还支持通过 **Agent 隧道 + 成对访问密钥** 把其它主机作为「子节点」纳入统一面板管理。

核心能力：实时系统监控、Docker 管理、网站（Nginx/OpenResty）管理、数据库管理、计划任务、防火墙、SSL、
日志、文件管理、Web 终端、应用商店、备份、通知中心、防篡改、WAF、多节点/SSH 密钥、内网穿透（Frp）等。

- 当前版本：`backend/app/main.py` 中的 `APP_VERSION = "1.4.1"`
- 许可证：AGPLv3
- Docker 镜像命名空间：`shunx/graw`

---

## 2. 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3（Composition API）、Vite 5、Axios、ECharts / vue-echarts、xterm.js、vue-i18n |
| 后端 | Python 3.11、FastAPI 0.115、Uvicorn、Pydantic 2、psutil、docker SDK |
| 通信 | REST API（`/api/*`）+ WebSocket（监控流、终端） |
| 部署 | 多阶段 Docker 构建（前端 Node 构建 → 后端 Python 运行时） |

依赖清单：`backend/requirements.txt`、`frontend/package.json`。

---

## 3. 目录结构（关键部分）

```
Graw/
├── backend/
│   ├── app/
│   │   ├── main.py              # 应用入口：路由注册、中间件、lifespan 后台任务、静态托管
│   │   ├── auth.py              # JWT 鉴权依赖：get_current_user / require_admin / seed_default_users
│   │   ├── agent_auth.py        # 子节点 Agent 机器间鉴权（/api/agent，成对密钥换 JWT）
│   │   ├── agent_cfg.py         # 子节点「收取模式」持久化配置（data/agent.json）
│   │   ├── agent_client.py      # 主面板 → 子节点的 Agent 隧道代理客户端
│   │   ├── node_manager.py      # 多节点管理：当前节点上下文、请求级 X-Graw-Node 覆盖
│   │   ├── remote_cap.py        # 远端子节点能力门控（local-only 接口防护）
│   │   ├── hostfs.py            # 宿主机文件系统适配层（chroot /host 读写宿主文件/命令）
│   │   ├── routers/             # 各业务模块路由（见下方清单）
│   │   └── data/                # 运行时数据（用户、密钥、节点凭据等，gitignore，权限收紧）
│   ├── reset_password.py        # 离线重置管理员密码脚本（读 data/users.json）
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── main.js              # 入口：createApp().use(i18n)
│   │   ├── App.vue              # 桌面环境根组件
│   │   ├── components/          # Desktop / Taskbar / WinWindow / 各类卡片
│   │   ├── components/windows/  # 每个功能的独立「窗口」组件（*Window.vue）
│   │   ├── store/               # 自定义 reactive 状态（auth/systemMetrics/docker/siteBus…）
│   │   ├── locales/             # vue-i18n 多语言（zh-TW/ja/ko/ru/de/fr/es/pt/eo…）
│   │   └── assets/style.css
│   ├── index.html
│   ├── vite.config.js           # 开发代理 /api(含 ws) → http://localhost:8000
│   └── package.json
├── app-store/                   # 社区应用商店的 YAML 配方与图标（安装即 docker compose）
├── Dockerfile                   # 多阶段构建（前端构建 → 后端运行时）
├── docker-compose.yml           # 「完整管理宿主机」高权限编排
├── start.sh / start.bat         # 本地开发一键启动（后端 + 前端）
└── README.md
```

### 后端路由清单（`backend/app/routers/`）

| 前缀 | 模块 | 鉴权 |
|------|------|------|
| `/api/auth` | 登录/当前用户/改密/用户管理 | 登录/管理员（公开登录） |
| `/api/agent` | 子节点 Agent 机器间鉴权（/issue 换 JWT） | 成对密钥 |
| `/api/system` | CPU/内存/磁盘/网络/负载 + WS 实时流 | 端点内自行鉴权（WS 用 `?token=`） |
| `/api/notes` | 备忘录 CRUD | PROTECTED（登录即可） |
| `/api/docker` `/api/dockervolumes` `/api/containeredit` | Docker 容器/镜像/卷/编辑 | ADMIN |
| `/api/terminal` | WebSocket 终端（paramiko） | 处理函数内 `?token=` + 强制管理员 |
| `/api/sites` `/api/sitesopts` `/api/rewrite` `/api/waf` `/api/webmode` `/api/webstats` | 网站/伪静态/缓存/WAF/引擎/统计 | ADMIN |
| `/api/databases` | MySQL/MariaDB/Redis 连接与查询 | ADMIN |
| `/api/cron` `/api/firewall` `/api/ssl` `/api/logs` `/api/backup` | 计划任务/防火墙/证书/日志/备份 | ADMIN |
| `/api/shunx` `/api/tamper` | ShunX 安全入口 / 网页防篡改（含 WS 告警） | 端点内自行鉴权 |
| `/api/appstore` `/api/tasks` `/api/runtime` | 应用商店 / 任务中心 / 运行时容器 | ADMIN |
| `/api/plugins` `/api/op` | 应用接口开放协议：插件管理（ADMIN，按 enabled 条件注册）/ 插件开放接口（端点内令牌鉴权）。`/api/plugins/settings` 总开关始终注册 | ADMIN / 令牌 |
| `/api/nodes` `/api/sshkeys` | 多节点管理 / SSH 密钥部署 | ADMIN |
| `/api/ui` `/api/vip` | 界面设置（公开/public + 管理员/config）/ VIP | 端点内自行鉴权 |
| `/api/frp` `/api/netstorage` `/api/update` `/api/notify` `/api/uptime` `/api/certcheck` | 内网穿透/网络储存/更新/通知/可用性/证书 | ADMIN |
| `/api/healthcheck` `/api/ftpusers` `/api/toolbox` `/api/phpversions` `/api/panelbackup` `/api/loginlog` `/api/svcmonitor` `/api/protected` | 体检/FTP用户/工具箱/PHP版本/面板备份/登录日志/服务监控 | ADMIN 或 PROTECTED |
| `/api/health` | 公开健康检查（版本号） | 公开 |

> 除 `/api/auth/login`、`/api/health` 外，所有接口均需 `Authorization: Bearer <token>`。

---

## 4. 环境要求与快速开始

### 环境要求
- Python 3.11（生产镜像）；本地开发 3.8+ 可跑后端
- Node.js 16+（前端）；Docker 引擎（Docker 管理功能可选）

### 本地开发（前后端分离）
```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端（另开终端）
cd frontend
npm install      # 或 pnpm install
npm run dev      # → http://localhost:5173，Vite 代理 /api 到 :8000
```


### 生产构建 / 部署
```bash
cd frontend && npm run build      # 输出 frontend/dist
# 后端会自动检测并挂载 frontend/dist 作为静态资源（SPA 回退到 index.html）
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 随后访问 http://localhost:8000 即完整应用
```
容器化部署见 `docker-compose.yml`（**高权限**，见第 7 节陷阱）。

### 默认账号
首次启动在 `backend/data/users.json` 自动播种：
- 账号 `admin` / 密码 `admin123`，首次登录强制改密。
- 签名密钥持久化于 `backend/data/secret.key`（首次自动生成）。
- 忘记密码可用 `cd backend && python reset_password.py admin` 离线重置。

---

## 5. 后端开发约定

### 5.1 路由注册与鉴权模型
- 所有业务路由集中在 `backend/app/main.py` 用 `app.include_router(..., prefix="/api/xxx")`。**新增模块必须在此注册**，否则前端无法访问。
- 鉴权分级（定义在 `main.py` 顶部）：
  - `PROTECTED = [Depends(get_current_user), Depends(require_non_default_password)]` —— 仅需登录（只读信息类）。
  - `ADMIN = [Depends(require_admin)]` —— 管理员（写操作/命令执行）。
  - 部分路由（WebSocket、终端、`/api/ui` 的 public、ShunX、VIP）在**端点内部**自行鉴权，不挂全局依赖，不要误加。
- `require_admin` 已级联 `require_non_default_password` + `get_current_user`，无需重复声明。

### 5.2 数据持久化
- 面板所有配置/凭据以 **JSON 文件** 存于 `backend/data/`（如 `users.json`、`nodes.json`、`databases.json`、`agent.json`、`ftp_users.json`）。
- 该目录含敏感信息（SSH 凭据、数据库密码、JWT 密钥）。启动时 `_secure_data_dir()` 在 Linux 上收紧为 `0700`/`0600`，**不要**在代码里放宽其权限。
- 写入配置时优先采用「原子写」：写临时文件后 `os.replace()`（参考 `agent_cfg._save()`）。

### 5.3 多节点 / Agent 架构（重点）
- **主面板 ↔ 子节点** 通过 Agent 隧道通信：
  - `agent_auth.py`：`/api/agent/issue` 用成对 `key+secret` 换取子节点 JWT（不依赖面板登录）。
  - `agent_cfg.py`：子节点「收取模式」持久化（`data/agent.json`），支持设置界面热开关，无需重启/改环境变量。
  - `agent_client.py`：`agent_proxy()` 把请求经隧道转发到子节点；`agent_ready()` 判断可用性。
  - `node_manager.py`：维护「当前管理节点」上下文；支持请求级 `X-Graw-Node` 头覆盖（统一面板按窗口聚焦节点下发）。
  - `remote_cap.py`：把某些接口标记为 local-only，防止远端子节点越权调用。
- 请求流向（`main.py` 中间件，由外到内）：
  1. `agent_proxy_middleware`（最外层）：当前节点为远程且已配 Agent 时，业务 HTTP 请求优先经隧道代理到子节点（WebSocket 升级不代理）。
  2. `remote_capability_guard`：local-only 接口门控（纵深防御）。
  3. 业务路由。
- **新增业务接口若需对子节点透传**，确认它不在 `_AGENT_PROXY_EXCLUDE_PREFIX`（auth/nodes/terminal/agent/ui/shunx/vip/health）中即可自动被代理，无需额外改动。

### 5.4 后台任务
- 在 `lifespan()` 中启停的常驻协程：系统指标采集（`system.start_metrics_producer`）、防篡改监控、通知、站点可用性、证书到期、服务监控。**新增常驻监控请在此配对启停**，避免泄漏协程。

### 5.5 安全中间件
- `security_headers` 给所有响应附加 CSP / `X-Content-Type-Options: nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: same-origin`。改动前端注入资源（如新 CDN）时需注意 CSP 限制（`script-src` 已放行 `unsafe-inline` + `unsafe-eval`）。

---

## 6. 前端开发约定

### 6.1 状态管理（无 Pinia）
- 状态使用**自定义 `reactive` 单例**，位于 `src/store/*.js`（如 `auth.js` 导出 `reactive` 的 `auth`，含 `setAuth/clearAuth/isAdmin`）。**不要引入 Pinia**，沿用该模式：新增全局状态就新建一个 `store/xxx.js` 导出 `reactive` 对象。

### 6.2 桌面式 UI
- `App.vue` 是桌面根；`components/Desktop.vue`、`Taskbar.vue`、`WinWindow.vue` 构成窗口系统。
- 每个功能是一个**独立窗口组件** `src/components/windows/*Window.vue`，由桌面/任务栏按需打开，支持拖拽、最大化/最小化。新增功能页应新建对应 `*Window.vue` 并在桌面注册入口。

### 6.3 API 调用
- 统一经 `src/api.js`（或各 store 内的 axios 封装）调用 `/api/*`，请求拦截器注入 `Authorization: Bearer <token>`（token 来自 `store/auth`）。
- 开发期 Vite 代理（`vite.config.js`）把 `/api` 与 WebSocket 转发到 `http://localhost:8000`；**生产无跨域**（前端由后端同源托管）。

### 6.4 国际化
- 使用 `vue-i18n`，语种文件在 `src/locales/`。新增界面文案应走 i18n key，**不要硬编码中文到模板**。

---

## 7. 关键约定与陷阱（务必注意）

1. **API 文档默认关闭**：`/docs`、`/redoc`、`/openapi.json` 在生产默认 `None`。调试时设环境变量 `GRAW_ENABLE_DOCS=1` 再访问 `/docs`。
2. **CORS 关闭**：`allow_origins=[]` + `allow_credentials=False`（同源部署）。不要为「方便本地联调」改成 `*`，这会暴露攻击面。
3. **WebSocket 鉴权用 `?token=`**：终端（`/api/terminal`）和监控 WS 无法在浏览器里带 Bearer 头，处理函数内用查询参数 token 鉴权并强制管理员。改动 WS 端点时保持此方式。
4. **`data/` 权限与凭据**：含 SSH/数据库/JWT 密钥，勿提交到仓库、勿放宽权限、勿明文回传前端（`agent_cfg.public_status()` 已脱敏，照此模式）。
5. **容器高权限（仅可信环境）**：`docker-compose.yml` 使用 `privileged` + `network_mode: host` + `pid: host` + `-v /:/host:rslave` + docker.sock。目的是让面板完整管理宿主机；**Docker Desktop for Windows 上这些无法完整生效**。开发阶段建议本地直接跑前后端，而非依赖该 compose。
6. **前端构建产物由后端托管**：`npm run build` 输出 `frontend/dist`，`main.py` 自动挂载 `/assets` 与 SPA 回退。改了前端后若生产不生效，先确认 `frontend/dist` 已生成。
7. **版本号单一来源**：升级版本只改 `main.py` 的 `APP_VERSION`；`/api/health` 在容器部署时优先读镜像 tag/label。
8. **不要绕过 remote_cap / Agent 代理**：设计新接口时明确它是 local-only 还是需要子节点透传，避免越权或代理失效。

---

## 8. 代码风格

- **后端**：类型注解 + Pydantic 模型；docstring 用中文描述背景与安全约束（项目大量此类注释，请保持）；日志用 `logging.getLogger("graw.xxx")`；敏感操作前做鉴权断言。
- **前端**：`<script setup>` Composition API；组件用 PascalCase；样式优先 scoped；避免在 `main.js` 之外挂载全局插件。
- 提交前确保 `backend` 无语法错误、前端 `npm run build` 通过。

---

## 9. 快速定位

| 我想做的事 | 去哪里 |
|-----------|--------|
| 加一个新 REST 业务模块 | 新建 `backend/app/routers/xxx.py`，在 `main.py` 注册并选 `PROTECTED`/`ADMIN` |
| 加一个前端功能页 | 新建 `frontend/src/components/windows/XxxWindow.vue` 并在桌面注册 |
| 改登录/鉴权/用户 | `backend/app/auth.py`、`routers/auth.py` |
| 调 Agent / 子节点接入 | `agent_auth.py`、`agent_cfg.py`、`agent_client.py`、`node_manager.py` |
| 改监控/指标采集 | `routers/system.py`、`store/systemMetrics.js` |
| 调应用商店配方 | `app-store/` 目录（YAML） |
| 改 Docker 构建 | `Dockerfile`、`docker-compose.yml` |
| 重置/查凭据/用户数据 | `backend/data/`、`reset_password.py` |
