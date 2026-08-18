# Graw

一個基於 Web 的伺服器管理面板，採用類桌面作業系統的互動設計。前端使用 Vue 3 + Vite，後端使用 FastAPI，提供即時系統監控、Docker 管理、進程管理、檔案管理、Web 終端與備忘錄等功能。

## 怎麼下載？

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

8041 改成你的通訊埠，Graw 在 Docker 使用統一封裝，只需要一個通訊埠即可使用，不需設定多筆記錄。

## 功能特性

- **帳號與權限系統** —— 基於 JWT 的使用者登入、角色（管理員/一般使用者）、帳號管理、強制改密，登入後所有受保護介面均需 `Authorization: Bearer <token>`
- **桌面式互動介面** —— 視窗化應用、工作列、桌面捷徑，支援拖曳、最大化/最小化
- **即時系統監控** —— CPU、記憶體、磁碟、網路、負載，透過 WebSocket 即時推送資料與圖表
- **網站管理** —— 支援 Nginx / Apache 虛擬主站的增刪改查、啟停、設定檔產生與檢視
- **資料庫管理** —— MySQL / MariaDB / Redis 連線管理、資料庫/資料表瀏覽、SQL / Redis 指令執行
- **計劃任務** —— Cron 運算式管理（Linux crontab / Windows schtasks 封裝）
- **防火牆** —— 通訊埠規則與 IP 黑名單/白名單管理（iptables / netsh）
- **SSL 憑證** —— 自訂憑證上傳與 Let's Encrypt 申請（certbot）
- **日誌中心** —— 系統日誌、網站日誌、面板日誌的即時檢視與清空
- **Docker 管理** —— 容器與映像檔的檢視、啟動、停止、日誌等操作
- **進程管理** —— 檢視系統執行中的進程列表與詳情
- **檔案管理** —— 瀏覽目錄、上傳下載、權限修改、壓縮解壓、複製重新命名
- **Web 終端** —— 基於 xterm.js 的瀏覽器內終端，直接操作伺服器（WebSocket 透過 `?token=` 鑑權）
- **備忘錄** —— 隨手記錄與檢視系統備忘資訊

## 技術棧

| 層級 | 技術 |
|------|------|
| 前端 | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| 後端 | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| 通訊 | REST API + WebSocket |

## 目錄結構

```
Graw/
├── frontend/          # Vue 3 前端
│   ├── src/
│   │   ├── components/     # 桌面、視窗、工作列、卡片元件
│   │   ├── api.js          # 後端 API 封裝
│   │   └── App.vue         # 根元件（桌面環境）
│   ├── package.json
│   └── vite.config.js
├── backend/           # FastAPI 後端
│   ├── app/
│   │   ├── main.py         # 應用程式入口
│   │   └── routers/        # 各模組路由（system, docker, process, files, terminal, notes）
│   ├── api/                # 相容舊版路由（可直接引用）
│   └── requirements.txt
├── start.bat          # Windows 一鍵啟動
├── start.sh           # Linux / macOS 一鍵啟動
└── README.md
```

## 快速開始

### 環境需求

- Python 3.8+
- Node.js 16+
- （可選）Docker 引擎，用於 Docker 管理功能

### 手動啟動

**1. 啟動後端**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # 首次
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 或者 如果你使用開發：
py start.py

```

**2. 啟動前端**

```bash
cd frontend
pnpm install
pnpm run dev
```

### 生產建置

前端生產建置輸出到 `frontend/dist`，後端會自動偵測並掛載該目錄作為靜態資源：

```bash
cd frontend
npm run build
```

隨後直接啟動後端即可透過 `http://localhost:8000` 存取完整應用程式。

## API 概覽

| 模組 | 前綴 | 說明 |
|------|------|------|
| Auth | `/api/auth` | 登入、目前使用者、改密、使用者管理（管理員） |
| System | `/api/system` | CPU、記憶體、磁碟、網路、負載、WebSocket 即時串流 |
| Sites | `/api/sites` | 網站虛擬主機管理（Nginx/Apache） |
| Databases | `/api/databases` | MySQL/MariaDB/Redis 連線與查詢管理 |
| Cron | `/api/cron` | 計劃任務（Linux crontab / Windows schtasks） |
| Firewall | `/api/firewall` | 通訊埠與 IP 防火牆規則 |
| SSL | `/api/ssl` | 自訂憑證上傳與 Let's Encrypt 申請 |
| Logs | `/api/logs` | 日誌檢視與清空 |
| Docker | `/api/docker` | 容器與映像檔管理 |
| Process | `/api/process` | 進程列表與詳情 |
| Files | `/api/files` | 檔案瀏覽、傳輸、權限、壓縮解壓 |
| Terminal | `/api/terminal` | WebSocket 終端工作階段（透過 `?token=` 鑑權） |
| Notes | `/api/notes` | 備忘錄 CRUD |

除 `/api/auth/login` 與 `/api/health` 外，所有介面均要求 `Authorization: Bearer <token>` 標頭。

## 預設帳號

首次啟動後會在 `backend/data/users.json` 中自動播種：

- 帳號：`admin`
- 密碼：`admin123`
- 狀態：首次登入後強制改密

簽名金鑰持久化在 `backend/data/secret.key`（首次啟動自動產生）。請在生產環境妥善保管該檔案及 `users.json`，並修改預設密碼。

詳細介面定義請參考 `backend/app/routers/` 下的各路由檔案。

## 重置密碼

如果忘記管理員密碼或無法登入 Web 面板，可以在伺服器本機直接執行命令列腳本重置密碼（無需啟動後端服務）：

```bash
cd backend

# 列出所有帳號
python reset_password.py --list

# 重置指定帳號（互動式輸入新密碼）
python reset_password.py admin

# 不指定帳號，腳本會提示選擇
python reset_password.py
```

腳本直接讀寫 `backend/data/users.json`，密碼輸入會隱藏，重置後自動清除「首次登入必須改密」標記。新密碼至少 6 位。

## 設定

前端開發伺服器的代理設定位於 `frontend/vite.config.js`，預設將 `/api` 與 WebSocket 轉送到 `http://localhost:8000`：

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

## 貢獻

歡迎提交 Issue 或 Pull Request。

## License

AGPLv3