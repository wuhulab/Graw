# Graw

一個以 Web 為基礎的伺服器管理面板，採用「類桌面作業系統」的互動設計（視窗、工作列、桌面捷徑），並內建一套 1Panel 風格的標準面板模式。前端使用 Vue 3 + Vite，後端使用 FastAPI。

除了本機之外，Graw 還能透過 **Agent 隧道 + 成對存取金鑰** 將其他主機以「子節點」形式納入統一的面板管理：在同一處即可切換主機，管理多台伺服器的容器、網站、檔案、終端與防火牆。

### 多語言 README

[簡體中文](../README.md) ·
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


## 相關連結

| 專案 | 位址 |
|------|------|
| 原始碼儲存庫 | <https://github.com/wuhulab/Graw> |
| 應用程式商店配方 | <https://github.com/wuhulab/Graw-app-store> |
| Docker 映像檔 | <https://hub.docker.com/r/shunx/graw> |
| 官方網站 | <https://graw.shunx.top/> |
| 問題回報 | <https://github.com/wuhulab/Graw/issues> |
| 捐贈支持 | <https://afdian.com/a/shunianssy> |

## 怎麼下載？

Graw 以容器方式執行，但它的**所有管理操作（Docker 容器/映像檔、應用程式商店安裝、Web 終端、處理程序/防火牆、網站設定等）都要作用於實體主機**。因此**不能**只寫 `-p 連接埠:8000` 那樣裸起容器，必須按下述「完整實體主機模式」啟動：讓容器能存取實體主機 Docker（socket）、實體主機根目錄（`/host`），並具備主機層級權限（`privileged` + `pid host`）。

**方式一：Linux 伺服器（建議，使用 host 網路直接監聽實體主機 8000 連接埠）**

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

啟動後請造訪 `http://<伺服器IP>:8000`。

**方式二：Bridge 網路（自訂存取連接埠，例如 8041）**

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

啟動後請造訪 `http://<伺服器IP>:8041`。

**方式三：Docker Compose（儲存庫內已提供高權限編排）**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

各參數含義（面板要完整管理實體主機所必需）：

- `--privileged`：授予容器全部核心能力，否則 `chroot /host`、iptables/防火牆、掛載等操作無法在容器內生效。
- `--pid host`：共享實體主機處理程序命名空間，處理程序管理/系統監控才能看到實體主機全部處理程序。
- `--network host`：使用實體主機網路，面板直接監聽實體主機 8000 連接埠（方式二改用 `-p 連接埠對應`）。
- `-v /:/host:rslave` + `HOST_ROOT=/host`：把實體主機根目錄掛進容器 `/host`，面板經 `chroot /host` 操作實體主機檔案與命令（nginx/certbot/crontab 等）。
- `-v /var/run/docker.sock:/var/run/docker.sock`：對接實體主機 Docker 引擎（容器/映像檔/日誌管理）。
- `/opt/graw/data` 為面板資料目錄（綁定到實體主機）；`GRAW_HOST_DATA=/opt/graw/data` 告知實體主機上的 docker-compose 檔案所在位置，Docker 應用程式商店才能完成安裝。

> ⚠️ **安全警告**：上述容器實質擁有實體主機 root 等級的操作能力，僅建議部署於可信任環境。請在首次登入後**立即修改預設密碼**，並妥善保護 `backend/data/` 下的憑證檔案。

## 功能特性

- **帳號與權限系統** —— 基於 JWT 的使用者登入、角色（管理員/一般使用者）、帳號管理、強制改密、登入日誌、線上工作階段管理（依 token 有效期與最後活躍時間判定線上）
- **雙介面形態** —— 類桌面模式（視窗/工作列/桌面捷徑，支援拖曳、最大化/最小化）與 1Panel 風格的標準面板模式（側邊欄分組選單 + 多分頁）
- **多節點管理** —— 主面板透過 Agent 隧道 + 成對存取金鑰納管子節點，支援 SSH 金鑰部署、請求級主機切換與遠端子節點能力閘控
- **即時系統監控** —— CPU、記憶體、磁碟、網路、負載，透過 WebSocket 即時推送資料與圖表，支援歷史指標查詢
- **網站管理** —— Nginx / OpenResty / Apache 虛擬主機的增刪改查、啟停、設定產生與檢視；可與 1Panel/OpenResty 共存並自動探索外部網站
- **WAF 與網站強化** —— Web 應用程式防火牆、偽靜態/rewrite、快取與網站強化設定、網站統計
- **資料庫管理** —— MySQL / MariaDB / Redis / PostgreSQL / MongoDB 連線管理、資料庫/資料表瀏覽、SQL / Redis 指令執行、慢查詢分析
- **Docker 管理** —— 容器與映像檔的檢視、啟動、停止、日誌、資源統計，相容 docker 與 podman 輸出格式
- **應用程式商店** —— 基於 YAML 配方一鍵安裝應用程式（安裝即 `docker compose`），支援自訂 compose 編輯與安裝日誌
- **檔案管理** —— 瀏覽目錄、上傳下載、權限修改、壓縮解壓、複製重新命名；Windows 風格剪貼簿（Ctrl+C/V/Delete）與拖曳資料夾上傳
- **資源回收筒** —— 誤刪檔案會進入資源回收筒，支援還原與依天數自動清理（跨節點）
- **Web 終端** —— 基於 xterm.js 的瀏覽器內終端，直接操作伺服器（WebSocket 透過 `?token=` 鑑權）
- **計劃任務 / 防火牆 / SSL** —— Cron 運算式管理（crontab / schtasks）、連接埠與 IP 黑名單/白名單（iptables / netsh，含 Docker 發佈連接埠的進出站管控）、憑證上傳與 Let's Encrypt 申請
- **安全與維運** —— ShunX 安全入口、網頁防篡改（WS 即時告警）、防火牆規則統一、健康檢查、面板備份、服務監控、憑證到期偵測、通知中心
- **其他** —— 日誌中心、處理程序管理、備忘錄、內網穿透（Frp）、網路儲存、FTP 使用者、PHP 版本管理、工具箱、外掛程式開放協議（GPOP）

## 技術棧

| 層級 | 技術 |
|------|------|
| 前端 | Vue 3（Composition API）、Vite 5、Axios、ECharts / vue-echarts、xterm.js、vue-i18n |
| 後端 | Python 3.11、FastAPI 0.115、Uvicorn、Pydantic 2、psutil、docker SDK |
| 通訊 | REST API（`/api/*`）+ WebSocket（監控串流、終端） |
| 部署 | 多階段 Docker 建置（前端 Node 建置 → 後端 Python 執行環境） |

## 目錄結構

```
Graw/
├── frontend/                 # Vue 3 前端
│   ├── src/
│   │   ├── components/       # 桌面、視窗、工作列、卡片元件
│   │   │   └── windows/      # 每個功能一個獨立視窗元件（*Window.vue）
│   │   ├── store/            # reactive 單例狀態（auth / systemMetrics / docker ...）
│   │   ├── locales/          # vue-i18n 多語言（共 22 種）
│   │   └── App.vue           # 根元件（桌面環境 / 面板模式切換）
│   ├── vite.config.js        # 開發代理 /api(含 ws) → :8000
│   └── package.json
├── backend/                  # FastAPI 後端
│   ├── app/
│   │   ├── main.py           # 應用程式入口：路由註冊、中介軟體、lifespan 背景任務
│   │   ├── auth.py           # JWT 鑑權依賴與使用者播種
│   │   ├── agent_*.py        # 子節點 Agent 鑑權 / 設定 / 隧道代理
│   │   ├── node_manager.py   # 多節點上下文與請求級主機切換
│   │   ├── hostfs.py         # 實體主機檔案系統適配層（chroot /host）
│   │   ├── routers/          # 各業務模組路由
│   │   └── data/             # 執行階段資料（gitignore，權限收緊）
│   ├── test_*_unit.py        # pytest 單元測試（test_*_e2e.py 為端到端案例）
│   └── requirements.txt
├── app-store/                # 社群應用程式商店 YAML 配方與圖示
├── plugin-examples/          # 外掛程式開放協議（GPOP）範例
├── readme-i18n/              # 多語言 README
├── docs/                     # 補充文件（外掛程式協議等）
├── agent/                    # 子節點 Agent 相關資源與技能
├── Dockerfile                # 多階段建置（前端建置 → 後端執行環境）
├── docker-compose.yml        # 「完整管理實體主機」高權限編排
├── start.sh / start.bat      # 本機開發一鍵啟動（後端 + 前端）
└── AGENTS.md                 # 程式庫架構與開發約定（改程式碼前必讀）
```

## 快速開始

### 環境需求

- Python 3.8+（生產映像檔為 3.11）
- Node.js 16+
- （選用）Docker 引擎，用於 Docker 管理功能

### 一鍵啟動（開發）

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

指令碼會在需要時建立虛擬環境、安裝相依套件，並同時拉起後端與前端。

### 手動啟動

**1. 啟動後端**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # 首次
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 或使用開發啟動指令碼
python start.py
```

**2. 啟動前端**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173，Vite 代理 /api 與 ws 到 :8000
```

> Windows 的 PowerShell/cmd 不支援 `&&`，多個命令請用分號 `;` 分隔。

### 生產建置

前端生產建置輸出到 `frontend/dist`，後端會自動偵測並掛載該目錄作為靜態資源：

```bash
cd frontend
npm run build
```

隨後直接啟動後端，即可透過 `http://localhost:8000` 存取完整應用程式（前端由後端同源託管，無需跨網域設定）。也可以直接使用儲存庫內的 `Dockerfile` / `docker-compose.yml` 建置映像檔部署。

## API 概覽

所有介面以 `/api/*` 為前綴，除 `/api/auth/login` 與 `/api/health` 外均要求 `Authorization: Bearer <token>` 標頭。

| 模組 | 前綴 | 說明 |
|------|------|------|
| Auth | `/api/auth` | 登入、目前使用者、改密、使用者管理（管理員） |
| Agent | `/api/agent` | 子節點機器間鑑權（成對金鑰換取 JWT） |
| System | `/api/system` | CPU、記憶體、磁碟、網路、負載、WebSocket 即時串流 |
| Nodes | `/api/nodes` | 多節點管理與目前管理主機切換 |
| Sites | `/api/sites` | 網站虛擬主機管理（Nginx / OpenResty / Apache） |
| WAF | `/api/waf` | Web 應用程式防火牆規則與站點防護 |
| Databases | `/api/databases` | MySQL / MariaDB / Redis / PostgreSQL / MongoDB 連線與查詢 |
| Docker | `/api/docker` | 容器、映像檔、磁碟區與容器編輯 |
| Files | `/api/files` | 檔案瀏覽、傳輸、權限、壓縮解壓 |
| Recycle | `/api/recycle` | 資源回收筒（還原、自動清理） |
| Terminal | `/api/terminal` | WebSocket 終端工作階段（透過 `?token=` 鑑權） |
| App Store | `/api/appstore` | 應用程式商店配方安裝與管理 |
| Cron / Firewall / SSL | `/api/cron`、`/api/firewall`、`/api/ssl` | 計劃任務 / 防火牆 / 憑證 |
| Plugins | `/api/plugins`、`/api/op` | 外掛程式管理與外掛程式開放介面（GPOP） |

完整路由清單與鑑權分級（`PROTECTED` / `ADMIN` / 端點內自鑑權）請見 [AGENTS.md](../AGENTS.md) 第 3 節。介面文件預設關閉，除錯時設定環境變數 `GRAW_ENABLE_DOCS=1` 後即可存取 `/docs`。

## 預設帳號

首次啟動後會在 `backend/data/users.json` 中自動播種：

- 帳號：`admin`
- 密碼：`admin123`
- 狀態：首次登入後強制改密

簽名金鑰持久化在 `backend/data/secret.key`（首次啟動自動產生）。請在生產環境妥善保管該檔案及 `users.json`，並修改預設密碼。

## 重置密碼

如果忘記管理員密碼或無法登入 Web 面板，可以在伺服器本機直接執行命令列指令碼重置密碼（無需啟動後端服務）：

```bash
cd backend

# 列出所有帳號
python reset_password.py --list

# 重置指定帳號（互動式輸入新密碼）
python reset_password.py admin

# 不指定帳號，指令碼會提示選擇
python reset_password.py
```

指令碼直接讀寫 `backend/data/users.json`，密碼輸入會隱藏，重置後自動清除「首次登入必須改密」標記。新密碼至少 6 位。

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

常用的環境變數：

| 變數 | 說明 |
|------|------|
| `HOST_ROOT` | 實體主機根目錄在容器內的掛載點（如 `/host`），啟用實體主機模式 |
| `GRAW_HOST_DATA` | 面板 `data` 目錄在實體主機上的實際路徑，應用程式商店安裝所需 |
| `GRAW_ENABLE_DOCS` | 設為 `1` 時開放 `/docs`、`/redoc`、`/openapi.json` |
| `GRAW_SESSION_ONLINE_SECONDS` | 工作階段線上判定閒置閾值（秒），預設 2 小時 |
| `TZ` | 容器時區，如 `Asia/Shanghai` |

## 專案文件

- [AGENTS.md](../AGENTS.md) —— 架構、約定與常見陷阱（**改動程式碼前請先通讀**）
- [CONTRIBUTING.md](../CONTRIBUTING.md) —— 貢獻指南與貢獻者授權協議（CLA）
- [SECURITY.md](../SECURITY.md) —— 安全性問題回報流程
- [CHANGELOG.md](../CHANGELOG.md) —— 版本變更記錄
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) —— 外掛程式開放協議（GPOP）
- [app-store/](../app-store/) —— 應用程式商店配方（YAML）
- [plugin-examples/](../plugin-examples/) —— 外掛程式範例


## 貢獻

歡迎提交 Issue 或 Pull Request，詳見 [CONTRIBUTING.md](../CONTRIBUTING.md)。

> **貢獻者授權協議（CLA）**：向本專案提交程式碼、文件或其他內容，即表示你**預設同意** [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla)，包括授權 **WuHuLaB** 保留將其貢獻用於**商業用途**及以**閉源（專有）授權**再授權/散布的權利。

## 捐贈

如果 Graw 對你有幫助，歡迎請作者喝杯咖啡 ☕

- 愛發電：<https://afdian.com/a/shunianssy>
- 雨雲（贊助商，便宜伺服器）：<https://www.rainyun.com/NjQwNjg5_>

## License

本專案以 [AGPLv3](../LICENSE) 開源發佈。

根據 [貢獻者授權協議（CLA）](../CONTRIBUTING.md#7-贡献者许可协议cla)，WuHuLaB 保留將本專案（含社群貢獻）用於**商業用途**並按**閉源（專有）授權**再授權或散布的權利。