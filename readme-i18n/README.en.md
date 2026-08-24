# Graw

A web-based server management panel with an operating-system-like desktop interaction design. The frontend uses Vue 3 + Vite; the backend uses FastAPI. It provides real-time system monitoring, Docker management, process management, file management, a web terminal, and notes.

## How to install?

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

Change `8041` to your port. Graw uses a unified Docker wrapper: only a single port is needed, and there is no need to configure multiple records.

## Features

- **Account and permission system** — JWT-based login, roles (administrator / normal user), account management, forced password change. All protected endpoints require `Authorization: Bearer <token>` after login
- **Desktop-style interface** — windowed apps, taskbar, desktop shortcuts, drag & drop, maximize / minimize
- **Real-time system monitoring** — CPU, memory, disk, network, load, pushed in real time over WebSocket with charts
- **Website management** — CRUD for Nginx / Apache virtual hosts, start/stop, config generation and preview
- **Database management** — MySQL / MariaDB / Redis connection management, database/table browsing, SQL / Redis command execution
- **Scheduled tasks** — Cron expression management (a wrapper over Linux crontab / Windows schtasks)
- **Firewall** — port rules and IP allow / deny lists (iptables / netsh)
- **SSL certificates** — upload custom certificates and request Let's Encrypt ones (certbot)
- **Log center** — real-time viewing and clearing of system, website, and panel logs
- **Docker management** — view containers and images; start, stop, view logs, and more
- **Process management** — view a list and details of running processes
- **File management** — browse directories, upload/download, change permissions, compress/extract, copy/rename
- **Web terminal** — an in-browser terminal based on xterm.js to operate the server directly (WebSocket authenticated via `?token=`)
- **Notes** — jot down and review system notes

## Tech stack

| Layer | Technology |
|------|------|
| Frontend | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| Backend | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| Communication | REST API + WebSocket |

## Directory structure

```
Graw/
├── frontend/          # Vue 3 frontend
│   ├── src/
│   │   ├── components/     # desktop, window, taskbar, card components
│   │   ├── api.js          # backend API wrapper
│   │   └── App.vue         # root component (desktop)
│   ├── package.json
│   └── vite.config.js
├── backend/           # FastAPI backend
│   ├── app/
│   │   ├── main.py         # application entry
│   │   └── routers/        # per-module routers (system, docker, process, files, terminal, notes)
│   ├── api/                # legacy-compatible routers (can be referenced directly)
│   └── requirements.txt
├── start.bat          # one-click start on Windows
├── start.sh           # one-click start on Linux / macOS
└── README.md
```

## Quick start

### Requirements

- Python 3.8+
- Node.js 16+
- (Optional) Docker engine, needed for the Docker management feature

### Manual start

**1. Start the backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # first time
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Or, for development use:
py start.py

```

**2. Start the frontend**

```bash
cd frontend
pnpm install
pnpm run dev
```

### Production build

The frontend production build outputs to `frontend/dist`; the backend automatically detects and serves this directory as static assets:

```bash
cd frontend
npm run build
```

Then start the backend and access the complete app at `http://localhost:8000`.

## API overview

| Module | Prefix | Description |
|------|------|------|
| Auth | `/api/auth` | Login, current user, change password, user management (admin) |
| System | `/api/system` | CPU, memory, disk, network, load, WebSocket real-time stream |
| Sites | `/api/sites` | Website virtual host management (Nginx/Apache) |
| Databases | `/api/databases` | MySQL/MariaDB/Redis connection and query management |
| Cron | `/api/cron` | Scheduled tasks (Linux crontab / Windows schtasks) |
| Firewall | `/api/firewall` | Port and IP firewall rules |
| SSL | `/api/ssl` | Upload custom certs and request Let's Encrypt |
| Logs | `/api/logs` | View and clear logs |
| Docker | `/api/docker` | Container and image management |
| Process | `/api/process` | Process list and details |
| Files | `/api/files` | File browsing, transfer, permissions, compression/extraction |
| Terminal | `/api/terminal` | WebSocket terminal sessions (authed via `?token=`) |
| Notes | `/api/notes` | Notes CRUD |

Except `/api/auth/login` and `/api/health`, all endpoints require an `Authorization: Bearer <token>` header.

## Default account

On first start, the app seeds `backend/data/users.json` automatically:

- Username: `admin`
- Password: `admin123`
- Status: forced password change after first login

The signing key is persisted at `backend/data/secret.key` (generated automatically on first start). Keep this file and `users.json` safe in production, and change the default password.

For detailed endpoint definitions, see the route files under `backend/app/routers/`.

## Reset password

If you forget the admin password or cannot log in to the web panel, you can reset the password directly from the server with a CLI script (no need to start the backend service):

```bash
cd backend

# List all accounts
python reset_password.py --list

# Reset a specific account (interactively enter the new password)
python reset_password.py admin

# Without an account, the script prompts you to choose
python reset_password.py
```

The script reads/writes `backend/data/users.json` directly, hides the password input, and automatically clears the "must change password on first login" flag after reset. The new password must be at least 6 characters.

## Configuration

The frontend dev server proxy config lives in `frontend/vite.config.js`; by default it forwards `/api` and WebSocket to `http://localhost:8000`:

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

## Contributing

Issues and pull requests are welcome.

## License

AGPLv3