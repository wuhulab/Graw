# Graw

Web ベースのサーバー管理パネルで、デスクトップ OS 風のインタラクションを採用しています。フロントエンドは Vue 3 + Vite、バックエンドは FastAPI を使用し、リアルタイムのシステム監視、Docker 管理、プロセス管理、ファイル管理、Web ターミナル、メモなどの機能を提供します。

## ダウンロード方法

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

`8041` をあなたのポートに変更してください。Graw は Docker で統一されたラッパーを使用し、1 つのポートだけで利用でき、複数のレコードを設定する必要はありません。

## 機能

- **アカウント・権限システム** — JWT ベースのログイン、ロール（管理者/一般ユーザー）、アカウント管理、初回ログイン時のパスワード強制変更。ログイン後、保護された API はすべて `Authorization: Bearer <token>` が必要
- **デスクトップ風 UI** — ウィンドウアプリ、タスクバー、デスクトップショートカット、ドラッグ、最大化/最小化をサポート
- **リアルタイムシステム監視** — CPU、メモリ、ディスク、ネットワーク、負荷を WebSocket でリアルタイムにグラフ表示
- **Web サイト管理** — Nginx / Apache 仮想ホストの作成・削除・起動停止・設定生成・表示
- **データベース管理** — MySQL / MariaDB / Redis の接続管理、DB/テーブル参照、SQL / Redis コマンド実行
- **スケジュールタスク** — Cron 式の管理（Linux crontab / Windows schtasks のラッパー）
- **ファイアウォール** — ポートルールと IP 許可/拒否リスト管理（iptables / netsh）
- **SSL 証明書** — 独自証明書のアップロードと Let's Encrypt 申請（certbot）
- **ログセンター** — システムログ、Web サイトログ、パネルログのリアルタイム表示とクリア
- **Docker 管理** — コンテナ・イメージの表示、起動、停止、ログ表示など
- **プロセス管理** — 実行中のプロセス一覧と詳細を表示
- **ファイル管理** — ディレクトリ参照、アップロード/ダウンロード、権限変更、圧縮/解凍、コピー/リネーム
- **Web ターミナル** — xterm.js ベースのブラウザ内ターミナルでサーバーを直接操作（WebSocket は `?token=` で認証）
- **メモ** — システムメモの記録と参照

## 技術スタック

| レイヤー | 技術 |
|------|------|
| フロントエンド | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| バックエンド | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| 通信 | REST API + WebSocket |

## ディレクトリ構成

```
Graw/
├── frontend/          # Vue 3 フロントエンド
│   ├── src/
│   │   ├── components/     # デスクトップ、ウィンドウ、タスクバー、カードコンポーネント
│   │   ├── api.js          # バックエンド API ラッパー
│   │   └── App.vue         # ルートコンポーネント（デスクトップ）
│   ├── package.json
│   └── vite.config.js
├── backend/           # FastAPI バックエンド
│   ├── app/
│   │   ├── main.py         # アプリケーションエントリ
│   │   └── routers/        # 各モジュールのルーター（system, docker, process, files, terminal, notes）
│   ├── api/                # 旧版互換ルーター（直接参照可能）
│   └── requirements.txt
├── start.bat          # Windows ワンクリック起動
├── start.sh           # Linux / macOS ワンクリック起動
└── README.md
```

## クイックスタート

### 必要環境

- Python 3.8+
- Node.js 16+
- （任意）Docker エンジン（Docker 管理機能に必要）

### 手動起動

**1. バックエンドを起動**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # 初回のみ
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# または、開発用として：
py start.py

```

**2. フロントエンドを起動**

```bash
cd frontend
pnpm install
pnpm run dev
```

### 本番ビルド

フロントエンドの本番ビルドは `frontend/dist` に出力され、バックエンドがこのディレクトリを静的リソースとして自動的に検出してマウントします：

```bash
cd frontend
npm run build
```

その後バックエンドを起動し、`http://localhost:8000` でフルアプリにアクセスできます。

## API 概要

| モジュール | プレフィックス | 説明 |
|------|------|------|
| Auth | `/api/auth` | ログイン、現在のユーザー、パスワード変更、ユーザー管理（管理者） |
| System | `/api/system` | CPU、メモリ、ディスク、ネットワーク、負荷、WebSocket リアルタイムストリーム |
| Sites | `/api/sites` | 仮想ホスト管理（Nginx/Apache） |
| Databases | `/api/databases` | MySQL/MariaDB/Redis の接続・クエリ管理 |
| Cron | `/api/cron` | スケジュールタスク（Linux crontab / Windows schtasks） |
| Firewall | `/api/firewall` | ポートと IP のファイアウォールルール |
| SSL | `/api/ssl` | 独自証明書のアップロードと Let's Encrypt 申請 |
| Logs | `/api/logs` | ログの表示とクリア |
| Docker | `/api/docker` | コンテナ・イメージ管理 |
| Process | `/api/process` | プロセス一覧と詳細 |
| Files | `/api/files` | ファイル参照、転送、権限、圧縮/解凍 |
| Terminal | `/api/terminal` | WebSocket ターミナルセッション（`?token=` で認証） |
| Notes | `/api/notes` | メモ CRUD |

`/api/auth/login` と `/api/health` を除き、すべてのエンドポイントは `Authorization: Bearer <token>` ヘッダーを必要とします。

## デフォルトアカウント

初回起動時に `backend/data/users.json` へ自動でシードされます：

- ユーザー名: `admin`
- パスワード: `admin123`
- 状態: 初回ログイン後にパスワード変更が強制されます

署名キーは `backend/data/secret.key` に永続化されます（初回起動時に自動生成）。本番環境ではこのファイルと `users.json` を安全に保管し、デフォルトパスワードを変更してください。

詳細な API 定義については `backend/app/routers/` 配下のルーターファイルを参照してください。

## パスワードリセット

管理者パスワードを忘れた、または Web パネルにログインできない場合は、サーバーのローカルで CLI スクリプトを直接実行してパスワードをリセットできます（バックエンドの起動は不要）：

```bash
cd backend

# 全アカウントを一覧表示
python reset_password.py --list

# 指定アカウントをリセット（対話的に新パスワード入力）
python reset_password.py admin

# アカウントを指定しない場合、スクリプトが選択を促します
python reset_password.py
```

スクリプトは `backend/data/users.json` を直接読み書きし、パスワード入力は非表示、リセット後に「初回ログイン時のパスワード変更必須」フラグを自動的にクリアします。新しいパスワードは 6 文字以上必要です。

## 設定

フロントエンド開発サーバーのプロキシ設定は `frontend/vite.config.js` にあり、デフォルトで `/api` と WebSocket を `http://localhost:8000` へ転送します：

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

## 貢献

Issue や Pull Request を歓迎します。

## License

AGPLv3