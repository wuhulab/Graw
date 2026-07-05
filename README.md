# Graw

一个基于 Web 的服务器管理面板，采用类桌面操作系统的交互设计。前端使用 Vue 3 + Vite，后端使用 FastAPI，提供实时系统监控、Docker 管理、进程管理、文件管理、Web 终端和备忘录等功能。

## 功能特性

- **账号与权限系统** —— 基于 JWT 的用户登录、角色（管理员/普通用户）、账号管理、强制改密，登录后所有受保护接口均需 `Authorization: Bearer <token>`
- **桌面式交互界面** —— 窗口化应用、任务栏、桌面快捷方式，支持拖拽、最大化/最小化
- **实时系统监控** —— CPU、内存、磁盘、网络、负载，通过 WebSocket 实时推送数据与图表
- **Docker 管理** —— 容器与镜像的查看、启动、停止、日志等操作
- **进程管理** —— 查看系统运行中的进程列表与详情
- **文件管理** —— 浏览目录、上传/下载文件
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
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt # 首次
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
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
| Docker | `/api/docker` | 容器与镜像管理 |
| Process | `/api/process` | 进程列表与详情 |
| Files | `/api/files` | 文件浏览与传输 |
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
