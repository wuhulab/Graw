# Graw

一个基于 Web 的服务器管理面板，采用类桌面操作系统的交互设计。前端使用 Vue 3 + Vite，后端使用 FastAPI，提供实时系统监控、Docker 管理、进程管理、文件管理、Web 终端和备忘录等功能。

## 怎么下载？

Graw 是以容器方式运行的服务器管理面板，但它的**所有管理操作（Docker 容器/镜像、Docker 应用商店安装、Web 终端、进程/防火墙等）都要作用于宿主机**。因此**不能**只用 `-p 端口:8000` 那样裸起容器，必须按下述「完整宿主机模式」启动：让容器能访问宿主机 Docker（socket）、宿主机根目录（`/host`）并具备宿主级权限（`privileged` + `pid host`）。

**Linux 服务器（推荐，`--network host` 直接监听宿主机 8000 端口）：**

```bash
docker run -d --name graw-panel \
  -p 8041:8000 --pid host --privileged \
  -v /opt/graw/data:/app/backend/data \
  -v /:/host:rslave \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e HOST_ROOT=/host \
  -e GRAW_HOST_DATA=/opt/graw/data \
  -e TZ=Asia/Shanghai \
  shunx/graw:latest
```

**Bridge 网络（自定义访问端口，例如 8041）：**

```bash
docker run -d --name graw-panel \
  -p 8041:8000 --pid host --privileged \
  -v /opt/graw/data:/app/backend/data \
  -v /:/host:rslave \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e HOST_ROOT=/host \
  -e GRAW_HOST_DATA=/opt/graw/data \
  -e TZ=Asia/Shanghai \
  shunx/graw:latest
```

各参数含义（面板完整管理宿主机所必需）：

- `--privileged`：授予容器全部内核能力，否则 `chroot /host`、iptables/防火墙、挂载等操作无法在容器内生效。
- `--pid host`：共享宿主机进程命名空间，进程管理/系统监控才能看到宿主机全部进程。
- `-v /:/host:rslave` + `HOST_ROOT=/host`：把宿主机根目录挂进容器 `/host`，面板经 `chroot /host` 操作宿主机文件与命令（nginx/certbot/crontab 等）。
- `-v /var/run/docker.sock:/var/run/docker.sock`：对接宿主机 Docker 引擎（容器/镜像/日志管理）。
- `/opt/graw/data` 为面板数据目录（绑定到宿主）；`GRAW_HOST_DATA=/opt/graw/data` 告知宿主机 docker-compose 文件所在，Docker 应用商店才能完成安装。

> 安全警告：上述容器实质拥有宿主机 root 级操作能力，仅部署于可信环境，请务必修改默认密码。

## 功能特性

- **账号与权限系统** —— 基于 JWT 的用户登录、角色（管理员/普通用户）、账号管理、强制改密，登录后所有受保护接口均需 `Authorization: Bearer <token>`
- **桌面式交互界面** —— 窗口化应用、任务栏、桌面快捷方式，支持拖拽、最大化/最小化
- **实时系统监控** —— CPU、内存、磁盘、网络、负载，通过 WebSocket 实时推送数据与图表
- **网站管理** —— 支持 Nginx / Apache 虚拟主站的增删改查、启停、配置生成与查看
- **数据库管理** —— MySQL / MariaDB / Redis 连接管理、库表浏览、SQL / Redis 命令执行
- **计划任务** —— Cron 表达式管理（Linux crontab / Windows schtasks 封装）
- **防火墙** —— 端口规则与 IP 黑白名单管理（iptables / netsh）
- **SSL 证书** —— 自定义证书上传与 Let's Encrypt 申请（certbot）
- **日志中心** —— 系统日志、网站日志、面板日志的实时查看与清空
- **Docker 管理** —— 容器与镜像的查看、启动、停止、日志等操作
- **进程管理** —— 查看系统运行中的进程列表与详情
- **文件管理** —— 浏览目录、上传下载、权限修改、压缩解压、复制重命名
- **Web 终端** —— 基于 xterm.js 的浏览器内终端，直接操作服务器（WebSocket 通过 `?token=` 鉴权）
- **备忘录** —— 随手记录与查看系统备注信息

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| 后端 | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| 通信 | REST API + WebSocket |

## 目录结构

```
Graw/
├── frontend/          # Vue 3 前端
│   ├── src/
│   │   ├── components/     # 桌面、窗口、任务栏、卡片组件
│   │   ├── api.js          # 后端 API 封装
│   │   └── App.vue         # 根组件（桌面环境）
│   ├── package.json
│   └── vite.config.js
├── backend/           # FastAPI 后端
│   ├── app/
│   │   ├── main.py         # 应用入口
│   │   └── routers/        # 各模块路由（system, docker, process, files, terminal, notes）
│   ├── api/                # 兼容旧版路由（可直接引用）
│   └── requirements.txt
├── start.bat          # Windows 一键启动
├── start.sh           # Linux / macOS 一键启动
└── README.md
```

## 快速开始

### 环境要求

- Python 3.8+
- Node.js 16+
- （可选）Docker 引擎，用于 Docker 管理功能

### 手动启动

**1. 启动后端**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # 首次
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 或者 如果你使用开发：
py start.py

```

**2. 启动前端**

```bash
cd frontend
pnpm install
pnpm run dev
```

### 生产构建

前端生产构建输出到 `frontend/dist`，后端会自动检测并挂载该目录作为静态资源：

```bash
cd frontend
npm run build
```

随后直接启动后端即可通过 `http://localhost:8000` 访问完整应用。

## API 概览

| 模块 | 前缀 | 说明 |
|------|------|------|
| Auth | `/api/auth` | 登录、当前用户、改密、用户管理（管理员） |
| System | `/api/system` | CPU、内存、磁盘、网络、负载、WebSocket 实时流 |
| Sites | `/api/sites` | 网站虚拟主机管理（Nginx/Apache） |
| Databases | `/api/databases` | MySQL/MariaDB/Redis 连接与查询管理 |
| Cron | `/api/cron` | 计划任务（Linux crontab / Windows schtasks） |
| Firewall | `/api/firewall` | 端口与 IP 防火墙规则 |
| SSL | `/api/ssl` | 自定义证书上传与 Let's Encrypt 申请 |
| Logs | `/api/logs` | 日志查看与清空 |
| Docker | `/api/docker` | 容器与镜像管理 |
| Process | `/api/process` | 进程列表与详情 |
| Files | `/api/files` | 文件浏览、传输、权限、压缩解压 |
| Terminal | `/api/terminal` | WebSocket 终端会话（通过 `?token=` 鉴权） |
| Notes | `/api/notes` | 备忘录 CRUD |

除 `/api/auth/login` 与 `/api/health` 外，所有接口均要求 `Authorization: Bearer <token>` 头。

## 默认账号

首次启动后会在 `backend/data/users.json` 中自动播种：

- 账号：`admin`
- 密码：`admin123`
- 状态：首次登录后强制改密

签名密钥持久化在 `backend/data/secret.key`（首次启动自动生成）。请在生产环境中妥善保管该文件及 `users.json`，并修改默认密码。

详细接口定义请参考 `backend/app/routers/` 下的各路由文件。

## 重置密码

如果忘记管理员密码或无法登录 Web 面板，可以在服务器本地直接运行命令行脚本重置密码（无需启动后端服务）：

```bash
cd backend

# 列出所有账号
python reset_password.py --list

# 重置指定账号（交互式输入新密码）
python reset_password.py admin

# 不指定账号，脚本会提示选择
python reset_password.py
```

脚本直接读写 `backend/data/users.json`，密码输入会隐藏，重置后自动清除“首次登录必须改密”标志。新密码至少 6 位。

## 配置

前端开发服务器的代理配置位于 `frontend/vite.config.js`，默认将 `/api` 与 WebSocket 转发到 `http://localhost:8000`：

```js
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
      ws: true
    }
  }
}
```

## 贡献

欢迎提交 Issue 或 Pull Request。

## License

AGPLv3
