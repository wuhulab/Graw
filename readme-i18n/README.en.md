# Graw

A web-based server management panel with an operating-system-like desktop interaction design (windows, taskbar, desktop shortcuts), and it also ships a 1Panel-style standard panel mode. The frontend uses Vue 3 + Vite; the backend uses FastAPI.

Beyond the local machine, Graw can also bring other hosts into a unified panel as "child nodes" through an **Agent tunnel + paired access keys**: switch hosts in one place and manage the containers, websites, files, terminals and firewalls of multiple servers.

### Multilingual README

[简体中文](../README.md) ·
[繁體中文](./README.zh-TW.md) ·
[English](./README.en.md) ·
[日本語](./README.ja.md) ·
[한국어](./README.ko.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md) ·
[Português](./README.pt.md) ·
[Esperanto](./README.eo.md)

## Related links

| Project | URL |
|------|------|
| Source repository | <https://github.com/wuhulab/Graw> |
| App Store recipes | <https://github.com/wuhulab/Graw-app-store> |
| Docker image | <https://hub.docker.com/r/shunx/graw> |
| Website | <https://graw.shunx.top/> |
| Issue tracker | <https://github.com/wuhulab/Graw/issues> |
| Donate | <https://afdian.com/a/shunianssy> |

## How to install?

Graw runs as a container, but **all of its management operations (Docker containers/images, App Store installation, web terminal, processes/firewall, website configuration, etc.) must act on the host machine**. Therefore you **cannot** just start a bare container with something like `-p port:8000`; you must launch it in the "full host mode" described below: the container must be able to access the host's Docker (socket) and the host's root directory (`/host`), and must have host-level privileges (`privileged` + `pid host`).

**Option 1: Linux server (recommended, using host networking to listen directly on host port 8000)**

```bash
docker run -d --name graw-panel \
  --network host --pid host --privileged \
  -v /opt/graw/data:/app/backend/data \
  -v /:/host:rslave \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e HOST_ROOT=/host \
  -e GRAW_HOST_DATA=/opt/graw/data \
  -e TZ=Asia/Shanghai \
  shunx/graw:latest
```

Once started, visit `http://<server IP>:8000`.

**Option 2: Bridge network (custom access port, for example 8041)**

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

Once started, visit `http://<server IP>:8041`.

**Option 3: Docker Compose (a high-privilege orchestration file is already provided in the repository)**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

Meaning of each parameter (required for the panel to fully manage the host):

- `--privileged`: grants the container all kernel capabilities; otherwise operations such as `chroot /host`, iptables/firewall and mounts cannot take effect inside the container.
- `--pid host`: shares the host process namespace, so process management/system monitoring can see all host processes.
- `--network host`: uses the host network, letting the panel listen directly on host port 8000 (Option 2 instead uses `-p` port mapping).
- `-v /:/host:rslave` + `HOST_ROOT=/host`: mounts the host root directory into the container at `/host`, so the panel can operate host files and commands (nginx/certbot/crontab, etc.) via `chroot /host`.
- `-v /var/run/docker.sock:/var/run/docker.sock`: connects to the host Docker engine (container/image/log management).
- `/opt/graw/data` is the panel data directory (bound to the host); `GRAW_HOST_DATA=/opt/graw/data` tells the host where the docker-compose file is located, which is required for the Docker App Store to complete installations.

> ⚠️ **Security warning**: The container above effectively has host root-level operational capability, so it should only be deployed in trusted environments. Please **change the default password immediately** after your first login, and properly safeguard the credential files under `backend/data/`.

## Features

- **Account and permission system** — JWT-based user login, roles (administrator / normal user), account management, forced password change, login log, online session management (online status is determined by token validity and last activity time)
- **Dual interface modes** — desktop-like mode (windows / taskbar / desktop shortcuts, with drag, maximize/minimize) and a 1Panel-style standard panel mode (sidebar grouped menu + multiple tabs)
- **Multi-node management** — the main panel manages child nodes through the Agent tunnel + paired access keys, with SSH key deployment, request-level host switching and remote child-node capability gating
- **Real-time system monitoring** — CPU, memory, disk, network and load, pushed in real time over WebSocket with data and charts, plus historical metric queries
- **Website management** — CRUD for Nginx / OpenResty / Apache virtual hosts, start/stop, config generation and preview; can coexist with 1Panel/OpenResty and automatically discovers external sites
- **WAF and website enhancements** — Web Application Firewall, rewrite rules, cache and site enhancement configuration, site statistics
- **Database management** — MySQL / MariaDB / Redis / PostgreSQL / MongoDB connection management, database/table browsing, SQL / Redis command execution, slow query analysis
- **Docker management** — view, start, stop, inspect logs and resource stats for containers and images; compatible with both docker and podman output formats
- **App Store** — one-click app installation from YAML recipes (installation is `docker compose`), with custom compose editing and installation logs
- **File management** — browse directories, upload/download, change permissions, compress/extract, copy/rename; Windows-style clipboard (Ctrl+C/V/Delete) and drag-and-drop folder upload
- **Recycle Bin** — accidentally deleted files go to the Recycle Bin, with restore and daily automatic cleanup (across nodes)
- **Web terminal** — an in-browser terminal based on xterm.js to operate the server directly (WebSocket authenticated via `?token=`)
- **Scheduled tasks / Firewall / SSL** — Cron expression management (crontab / schtasks), port and IP allow/deny lists (iptables / netsh, including inbound/outbound control for Docker published ports), certificate upload and Let's Encrypt requests
- **Security and operations** — ShunX secure entry, web page tamper protection (WS real-time alerts), unified firewall rules, health checks, panel backup, service monitoring, certificate expiry detection, notification center
- **Others** — log center, process management, notes, intranet penetration (Frp), network storage, FTP users, PHP version management, toolbox, plugin open protocol (GPOP)

## Tech stack

| Layer | Technology |
|------|------|
| Frontend | Vue 3 (Composition API), Vite 5, Axios, ECharts / vue-echarts, xterm.js, vue-i18n |
| Backend | Python 3.11, FastAPI 0.115, Uvicorn, Pydantic 2, psutil, docker SDK |
| Communication | REST API (`/api/*`) + WebSocket (monitoring stream, terminal) |
| Deployment | Multi-stage Docker build (frontend Node build → backend Python runtime) |

## Directory structure

```
Graw/
├── frontend/                 # Vue 3 frontend
│   ├── src/
│   │   ├── components/       # desktop, window, taskbar, card components
│   │   │   └── windows/      # one independent window component per feature (*Window.vue)
│   │   ├── store/            # reactive singleton state (auth / systemMetrics / docker ...)
│   │   ├── locales/          # vue-i18n locales (22 in total)
│   │   └── App.vue           # root component (desktop environment / panel mode switching)
│   ├── vite.config.js        # dev proxy /api (incl. ws) → :8000
│   └── package.json
├── backend/                  # FastAPI backend
│   ├── app/
│   │   ├── main.py           # application entry: route registration, middleware, lifespan background tasks
│   │   ├── auth.py           # JWT auth dependencies and user seeding
│   │   ├── agent_*.py        # child-node Agent auth / config / tunnel proxy
│   │   ├── node_manager.py   # multi-node context and request-level host switching
│   │   ├── hostfs.py         # host filesystem adapter layer (chroot /host)
│   │   ├── routers/          # per-module business routers
│   │   └── data/             # runtime data (gitignored, tightened permissions)
│   ├── test_*_unit.py        # pytest unit tests (test_*_e2e.py are end-to-end cases)
│   └── requirements.txt
├── app-store/                # community App Store YAML recipes and icons
├── plugin-examples/          # plugin open protocol (GPOP) examples
├── readme-i18n/              # multilingual READMEs
├── docs/                     # supplementary docs (plugin protocol, etc.)
├── agent/                    # child-node Agent resources and skills
├── Dockerfile                # multi-stage build (frontend build → backend runtime)
├── docker-compose.yml        # "full host management" high-privilege orchestration
├── start.sh / start.bat      # one-click local dev start (backend + frontend)
└── AGENTS.md                 # codebase architecture and dev conventions (must-read before changing code)
```

## Quick start

### Requirements

- Python 3.8+ (production image is 3.11)
- Node.js 16+
- (Optional) Docker engine, for the Docker management feature

### One-click start (development)

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

The script creates a virtual environment and installs dependencies when needed, and starts both the backend and the frontend.

### Manual start

**1. Start the backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # first time only
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# or use the development startup script
python start.py
```

**2. Start the frontend**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173, Vite proxies /api and ws to :8000
```

> On Windows, PowerShell/cmd does not support `&&`; separate multiple commands with a semicolon `;`.

### Production build

The frontend production build outputs to `frontend/dist`, and the backend automatically detects and mounts that directory as static assets:

```bash
cd frontend
npm run build
```

Then simply start the backend and access the complete app at `http://localhost:8000` (the frontend is served same-origin by the backend, with no CORS configuration). You can also build and deploy the image directly with the `Dockerfile` / `docker-compose.yml` provided in the repository.

## API overview

All endpoints are prefixed with `/api/*`; except for `/api/auth/login` and `/api/health`, all of them require an `Authorization: Bearer <token>` header.

| Module | Prefix | Description |
|------|------|------|
| Auth | `/api/auth` | Login, current user, change password, user management (admin) |
| Agent | `/api/agent` | Child-node machine-to-machine auth (paired secret exchanged for JWT) |
| System | `/api/system` | CPU, memory, disk, network, load, WebSocket real-time stream |
| Nodes | `/api/nodes` | Multi-node management and current managed host switching |
| Sites | `/api/sites` | Website virtual host management (Nginx / OpenResty / Apache) |
| WAF | `/api/waf` | Web Application Firewall rules and site protection |
| Databases | `/api/databases` | MySQL / MariaDB / Redis / PostgreSQL / MongoDB connections and queries |
| Docker | `/api/docker` | Containers, images, volumes and container editing |
| Files | `/api/files` | File browsing, transfer, permissions, compression/extraction |
| Recycle | `/api/recycle` | Recycle Bin (restore, automatic cleanup) |
| Terminal | `/api/terminal` | WebSocket terminal sessions (authenticated via `?token=`) |
| App Store | `/api/appstore` | App Store recipe installation and management |
| Cron / Firewall / SSL | `/api/cron`, `/api/firewall`, `/api/ssl` | Scheduled tasks / firewall / certificates |
| Plugins | `/api/plugins`, `/api/op` | Plugin management and plugin open interface (GPOP) |

For the complete route list and authentication levels (`PROTECTED` / `ADMIN` / endpoint-level self-auth), see section 3 of [AGENTS.md](../AGENTS.md). API docs are disabled by default; for debugging, set the environment variable `GRAW_ENABLE_DOCS=1` and then visit `/docs`.

## Default account

On first start, the following is seeded automatically in `backend/data/users.json`:

- Username: `admin`
- Password: `admin123`
- Status: forced password change after first login

The signing key is persisted at `backend/data/secret.key` (generated automatically on first start). In production, keep this file and `users.json` safe, and change the default password.

## Reset password

If you forget the admin password or cannot log in to the web panel, you can reset it by running a CLI script directly on the server (no need to start the backend service):

```bash
cd backend

# List all accounts
python reset_password.py --list

# Reset a specific account (interactively enter the new password)
python reset_password.py admin

# Without specifying an account, the script will prompt you to choose
python reset_password.py
```

The script reads/writes `backend/data/users.json` directly, hides the password input, and automatically clears the "must change password on first login" flag after reset. The new password must be at least 6 characters.

## Configuration

The frontend dev server proxy configuration lives in `frontend/vite.config.js`; by default it forwards `/api` and WebSocket to `http://localhost:8000`:

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

Commonly used environment variables:

| Variable | Description |
|------|------|
| `HOST_ROOT` | Mount point of the host root directory inside the container (e.g. `/host`); enables host mode |
| `GRAW_HOST_DATA` | Actual path of the panel `data` directory on the host; required for App Store installation |
| `GRAW_ENABLE_DOCS` | When set to `1`, exposes `/docs`, `/redoc`, `/openapi.json` |
| `GRAW_SESSION_ONLINE_SECONDS` | Idle threshold (seconds) for determining whether a session is online; default 2 hours |
| `TZ` | Container timezone, e.g. `Asia/Shanghai` |

## Project documentation

- [AGENTS.md](../AGENTS.md) — architecture, conventions and common pitfalls (**please read through before changing code**)
- [CONTRIBUTING.md](../CONTRIBUTING.md) — contribution guide and Contributor License Agreement (CLA)
- [SECURITY.md](../SECURITY.md) — security issue reporting process
- [CHANGELOG.md](../CHANGELOG.md) — version changelog
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) — plugin open protocol (GPOP)
- [app-store/](../app-store/) — App Store recipes (YAML)
- [plugin-examples/](../plugin-examples/) — plugin examples

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](../CONTRIBUTING.md) for details.

> **Contributor License Agreement (CLA)**: By submitting code, documentation or any other content to this project, you **automatically agree** to the [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla), including authorizing **WuHuLaB** to retain the right to use your contribution for **commercial purposes** and to relicense/distribute it under a **closed-source (proprietary) license**.

## Donate

If Graw has been helpful to you, you're welcome to buy the author a cup of coffee ☕

- Afdian: <https://afdian.com/a/shunianssy>
- RainYun (sponsor, affordable servers): <https://www.rainyun.com/NjQwNjg5_>

## License

This project is released as open source under [AGPLv3](../LICENSE).

Under the [Contributor License Agreement (CLA)](../CONTRIBUTING.md#7-贡献者许可协议cla), WuHuLaB retains the right to use this project (including community contributions) for **commercial purposes** and to relicense or distribute it under a **closed-source (proprietary) license**.