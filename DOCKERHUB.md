# Graw

**Graw** is an open-source, self-hosted **server management panel** with a desktop-like UI — real-time monitoring, Docker & App Store, websites, databases, web terminal and multi-node management in one panel. Built with Vue 3 + FastAPI.

![Graw — desktop-like server management panel](https://graw.shunx.top/assets/graw-hero.jpg)

> 中文文档：[GitHub 中文 README](https://github.com/wuhulab/Graw/blob/main/README.md) ·
> Source & docs: [github.com/wuhulab/Graw](https://github.com/wuhulab/Graw) · Website: [graw.shunx.top](https://graw.shunx.top/)

---

## Important: run Graw in "full host mode"

Graw manages the **host machine**: Docker containers/images, App Store installations, the web terminal, processes/firewall rules and website configuration all act on the host. A plain `-p 8000:8000` container will **not** work. The container needs:

- the host Docker socket (`/var/run/docker.sock`)
- the host root directory mounted at `/host`
- host-level privileges (`--privileged` + `--pid host`)

## Quick start

**Option 1 — Host network (recommended, listens on host port 8000):**

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

Then open `http://<server-ip>:8000`.

**Option 2 — Bridge network (custom port, e.g. 8041):**

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

Then open `http://<server-ip>:8041`.

> **Security warning:** the container effectively has host root-level access. Deploy it only in trusted environments, and change the default password right after the first login.

## First login

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `admin123` |
| Password change | Required after the first login |

Forgot the password? Reset it offline from inside the container:

```bash
docker exec -it graw-panel python reset_password.py admin
```

## Volumes & ports

| Container path | Purpose |
|----------------|---------|
| `/app/backend/data` | Panel data (users, credentials, configs, reports) — bind it to a host directory such as `/opt/graw/data` |
| `/host` | Host root filesystem — required, mount with `-v /:/host:rslave` |
| `/var/run/docker.sock` | Host Docker engine — required for Docker management and App Store installs |
| `8000/tcp` | Panel web UI (HTTP) |

## Environment variables

| Variable | Description |
|----------|-------------|
| `HOST_ROOT` | Mount point of the host root inside the container (e.g. `/host`). Enables host mode. |
| `GRAW_HOST_DATA` | Real path of the panel data directory on the host (e.g. `/opt/graw/data`). Required for App Store installations. |
| `TZ` | Container timezone, e.g. `Asia/Shanghai`. |
| `GRAW_ENABLE_DOCS` | Set to `1` to expose API docs at `/docs`. Disabled by default. |
| `GRAW_SESSION_ONLINE_SECONDS` | Idle threshold (seconds) used to decide whether a session is online. Default `7200` (2 hours). |
| `GRAW_MAX_BODY_MB` | Request body size limit (MB) for normal requests. Default `16`. |
| `GRAW_MAX_UPLOAD_MB` | Upload size limit (MB) for multipart uploads. Default `2048`. |

## Image tags & architectures

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable release |
| `1.7.2`, `1.7.1`, ... | Pinned versions, matching the GitHub release tags |

Supported architectures: `linux/amd64`, `linux/arm64`.

## Features

- **Dual interface modes** — desktop-like mode (windows / taskbar / desktop shortcuts) and a classic sidebar-style standard panel mode, switchable in Settings
- **Real-time system monitoring** — CPU, memory, disk, network and load, pushed over WebSocket with live charts
- **Docker management & App Store** — containers, images, volumes, logs, stats; one-click app installs from YAML recipes (`docker compose`)
- **Website management** — Nginx / OpenResty / Apache virtual hosts, WAF, rewrite rules, site analytics; auto-discovers existing OpenResty sites
- **Databases** — MySQL / MariaDB / Redis / PostgreSQL / MongoDB connections, browsing, SQL & Redis commands, slow query analysis
- **File manager & recycle bin** — upload/download, permissions, compress/extract, drag-and-drop folder upload; deleted files go to a recycle bin
- **Web terminal** — in-browser terminal (xterm.js) to operate the server directly
- **Scheduled tasks / firewall / SSL** — cron jobs, port & IP allow/deny lists (incl. Docker published ports), certificate upload and Let's Encrypt
- **Multi-node management** — manage other hosts as child nodes through an Agent tunnel with paired access keys
- **Security & ops** — tamper protection, health checks, panel backup, service monitoring, certificate expiry detection, notification center

![Graw — desktop-like multi-window workspace](https://graw.shunx.top/assets/graw-desktop.jpg)

## Links

- **GitHub:** [github.com/wuhulab/Graw](https://github.com/wuhulab/Graw)
- **Website:** [graw.shunx.top](https://graw.shunx.top/)
- **Documentation:** [docs/](https://github.com/wuhulab/Graw/tree/main/docs)
- **Issues & feedback:** [github.com/wuhulab/Graw/issues](https://github.com/wuhulab/Graw/issues/new/choose)
- **Changelog:** [CHANGELOG.md](https://github.com/wuhulab/Graw/blob/main/CHANGELOG.md)

## License

Released under [AGPLv3](https://github.com/wuhulab/Graw/blob/main/LICENSE).