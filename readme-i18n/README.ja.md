# Graw

Web ベースのサーバー管理パネルで、「デスクトップ OS ライク」なインタラクション設計（ウィンドウ、タスクバー、デスクトップショートカット）を採用し、さらに 1Panel 風の標準パネルモードを内蔵しています。フロントエンドは Vue 3 + Vite、バックエンドは FastAPI を使用します。

Graw はローカルマシンだけでなく、**Agent トンネル + ペアリングされたアクセスキー** によって他のホストを「子ノード」として統一面板管理に取り込めます。1 か所でホストを切り替え、複数サーバーのコンテナ、Web サイト、ファイル、ターミナル、ファイアウォールを管理できます。

### 多言語 README

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

## 関連リンク

| 項目 | アドレス |
|------|------|
| ソースリポジトリ | <https://github.com/wuhulab/Graw> |
| アプリストアのレシピ | <https://github.com/wuhulab/Graw-app-store> |
| Docker イメージ | <https://hub.docker.com/r/shunx/graw> |
| 公式サイト | <https://graw.shunx.top/> |
| 問題報告 | <https://github.com/wuhulab/Graw/issues> |
| 寄付サポート | <https://afdian.com/a/shunianssy> |

## ダウンロード方法

Graw はコンテナとして動作しますが、**そのすべての管理操作（Docker コンテナ/イメージ、アプリストアのインストール、Web ターミナル、プロセス/ファイアウォール、Web サイト設定など）はホストマシンに対して行われる必要があります**。そのため、`-p ポート:8000` のように裸のコンテナを起動するだけでは**不十分**で、以下に示す「完全ホストモード」で起動する必要があります。コンテナがホストの Docker（socket）とホストのルートディレクトリ（`/host`）にアクセスでき、かつホストレベルの権限（`privileged` + `pid host`）を持つようにします。

**方法 1：Linux サーバー（推奨。host ネットワークでホストの 8000 番ポートを直接リッスン）**

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

起動後、`http://<サーバー IP>:8000` にアクセスします。

**方法 2：Bridge ネットワーク（アクセス用ポートをカスタマイズ、例：8041）**

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

起動後、`http://<サーバー IP>:8041` にアクセスします。

**方法 3：Docker Compose（リポジトリに高権限のオーケストレーションを同梱）**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

各パラメータの意味（パネルがホストを完全に管理するために必須）：

- `--privileged`：コンテナにすべてのカーネル権限を付与します。これがないと `chroot /host`、iptables/ファイアウォール、マウントなどの操作がコンテナ内で有効になりません。
- `--pid host`：ホストのプロセス名前空間を共有し、プロセス管理/システム監視でホストのすべてのプロセスを確認できるようにします。
- `--network host`：ホストのネットワークを使用し、パネルがホストの 8000 番ポートを直接リッスンします（方法 2 では `-p ポートマッピング` を使用）。
- `-v /:/host:rslave` + `HOST_ROOT=/host`：ホストのルートディレクトリをコンテナの `/host` にマウントし、パネルが `chroot /host` 経由でホストのファイルとコマンド（nginx/certbot/crontab など）を操作できるようにします。
- `-v /var/run/docker.sock:/var/run/docker.sock`：ホストの Docker エンジンに接続します（コンテナ/イメージ/ログ管理）。
- `/opt/graw/data` はパネルのデータディレクトリ（ホストにバインド）です。`GRAW_HOST_DATA=/opt/graw/data` はホスト側の docker-compose ファイルの場所を伝え、Docker アプリストアがインストールを完了できるようにします。

> ⚠️ **セキュリティ警告**：上記のコンテナは事実上ホストの root レベルの操作権限を持ちます。信頼できる環境にのみデプロイすることを推奨します。初回ログイン後は**ただちにデフォルトパスワードを変更**し、`backend/data/` 配下の認証情報ファイルを適切に保護してください。

## 機能

- **アカウント・権限システム** —— JWT ベースのユーザーログイン、ロール（管理者/一般ユーザー）、アカウント管理、パスワード変更の強制、ログインログ、オンラインセッション管理（トークンの有効期間と最終アクティブ時刻でオンライン判定）
- **2 つの UI 形態** —— デスクトップ風モード（ウィンドウ/タスクバー/デスクトップショートカット、ドラッグ、最大化/最小化に対応）と 1Panel 風の標準パネルモード（サイドバーのグループメニュー + マルチタブ）
- **マルチノード管理** —— メインパネルが Agent トンネル + ペアリングされたアクセスキーで子ノードを管理。SSH キー配備、リクエスト単位のホスト切り替え、リモート子ノードの機能ゲーティングに対応
- **リアルタイムシステム監視** —— CPU、メモリ、ディスク、ネットワーク、負荷を WebSocket でリアルタイムにデータとグラフとして配信。履歴メトリクスの参照にも対応
- **Web サイト管理** —— Nginx / OpenResty / Apache 仮想ホストの作成・削除・変更・参照、起動/停止、設定生成と表示。1Panel/OpenResty と共存でき、外部サイトを自動検出
- **WAF とサイト強化** —— Web アプリケーションファイアウォール、rewrite ルール、キャッシュとサイト強化設定、サイト統計
- **データベース管理** —— MySQL / MariaDB / Redis / PostgreSQL / MongoDB の接続管理、DB/テーブル参照、SQL / Redis コマンド実行、スロークエリ分析
- **Docker 管理** —— コンテナとイメージの表示、起動、停止、ログ、リソース統計。docker と podman の出力形式の両方に対応
- **アプリストア** —— YAML レシピによるワンクリックアプリインストール（インストールは `docker compose`）。カスタム compose 編集とインストールログに対応
- **ファイル管理** —— ディレクトリ参照、アップロード/ダウンロード、権限変更、圧縮/解凍、コピー/リネーム。Windows 風クリップボード（Ctrl+C/V/Delete）とフォルダのドラッグ&ドロップアップロード
- **ごみ箱** —— 誤って削除したファイルはごみ箱に入り、復元と日次自動クリーンアップに対応（ノードをまたいで動作）
- **Web ターミナル** —— xterm.js ベースのブラウザ内ターミナルでサーバーを直接操作（WebSocket は `?token=` で認証）
- **スケジュールタスク / ファイアウォール / SSL** —— Cron 式の管理（crontab / schtasks）、ポートと IP の許可/拒否リスト（iptables / netsh、Docker 公開ポートのインバウンド/アウトバウンド制御を含む）、証明書アップロードと Let's Encrypt 申請
- **セキュリティと運用** —— ShunX セキュリティエントリ、Web ページ改ざん防止（WS リアルタイムアラート）、ファイアウォールルールの一元化、ヘルスチェック、パネルバックアップ、サービス監視、証明書有効期限検出、通知センター
- **その他** —— ログセンター、プロセス管理、メモ、イントラネットトンネリング（Frp）、ネットワークストレージ、FTP ユーザー、PHP バージョン管理、ツールボックス、プラグインオープンプロトコル（GPOP）

## 技術スタック

| レイヤー | 技術 |
|------|------|
| フロントエンド | Vue 3（Composition API）、Vite 5、Axios、ECharts / vue-echarts、xterm.js、vue-i18n |
| バックエンド | Python 3.11、FastAPI 0.115、Uvicorn、Pydantic 2、psutil、docker SDK |
| 通信 | REST API（`/api/*`）+ WebSocket（監視ストリーム、ターミナル） |
| デプロイ | マルチステージ Docker ビルド（フロントエンド Node ビルド → バックエンド Python ランタイム） |

## ディレクトリ構成

```
Graw/
├── frontend/                 # Vue 3 フロントエンド
│   ├── src/
│   │   ├── components/       # デスクトップ、ウィンドウ、タスクバー、カードコンポーネント
│   │   │   └── windows/      # 機能ごとに独立したウィンドウコンポーネント（*Window.vue）
│   │   ├── store/            # reactive シングルトン状態（auth / systemMetrics / docker ...）
│   │   ├── locales/          # vue-i18n 多言語（全 22 種）
│   │   └── App.vue           # ルートコンポーネント（デスクトップ環境 / パネルモード切り替え）
│   ├── vite.config.js        # 開発プロキシ /api（ws 含む）→ :8000
│   └── package.json
├── backend/                  # FastAPI バックエンド
│   ├── app/
│   │   ├── main.py           # アプリケーションエントリ：ルート登録、ミドルウェア、lifespan バックグラウンドタスク
│   │   ├── auth.py           # JWT 認証依存関係とユーザーシード
│   │   ├── agent_*.py        # 子ノード Agent の認証 / 設定 / トンネルプロキシ
│   │   ├── node_manager.py   # マルチノードコンテキストとリクエスト単位のホスト切り替え
│   │   ├── hostfs.py         # ホストファイルシステム適応層（chroot /host）
│   │   ├── routers/          # 各業務モジュールのルーター
│   │   └── data/             # ランタイムデータ（gitignore、権限を厳格化）
│   ├── test_*_unit.py        # pytest ユニットテスト（test_*_e2e.py はエンドツーエンドケース）
│   └── requirements.txt
├── app-store/                # コミュニティアプリストアの YAML レシピとアイコン
├── plugin-examples/          # プラグインオープンプロトコル（GPOP）のサンプル
├── readme-i18n/              # 多言語 README
├── docs/                     # 補足ドキュメント（プラグインプロトコルなど）
├── agent/                    # 子ノード Agent 関連のリソースとスキル
├── Dockerfile                # マルチステージビルド（フロントエンドビルド → バックエンドランタイム）
├── docker-compose.yml        # 「完全ホスト管理」高権限オーケストレーション
├── start.sh / start.bat      # ローカル開発ワンクリック起動（バックエンド + フロントエンド）
└── AGENTS.md                 # コードベースのアーキテクチャと開発規約（コード変更前に必読）
```

## クイックスタート

### 必要環境

- Python 3.8+（本番イメージは 3.11）
- Node.js 16+
- （任意）Docker エンジン（Docker 管理機能に必要）

### ワンクリック起動（開発）

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

スクリプトは必要に応じて仮想環境を作成し、依存関係をインストールしたうえで、バックエンドとフロントエンドを同時に起動します。

### 手動起動

**1. バックエンドを起動**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # 初回のみ
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# または開発用起動スクリプトを使用
python start.py
```

**2. フロントエンドを起動**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173、Vite が /api と ws を :8000 にプロキシ
```

> Windows の PowerShell/cmd は `&&` をサポートしていません。複数のコマンドはセミコロン `;` で区切ってください。

### 本番ビルド

フロントエンドの本番ビルドは `frontend/dist` に出力され、バックエンドがそのディレクトリを静的リソースとして自動検出してマウントします：

```bash
cd frontend
npm run build
```

その後バックエンドを起動するだけで、`http://localhost:8000` から完全なアプリにアクセスできます（フロントエンドはバックエンドが同一オリジンで配信するため、CORS 設定は不要です）。リポジトリ内の `Dockerfile` / `docker-compose.yml` を使ってイメージをビルドしデプロイすることもできます。

## API 概要

すべてのエンドポイントは `/api/*` をプレフィックスとし、`/api/auth/login` と `/api/health` を除き、すべて `Authorization: Bearer <token>` ヘッダーを必要とします。

| モジュール | プレフィックス | 説明 |
|------|------|------|
| Auth | `/api/auth` | ログイン、現在のユーザー、パスワード変更、ユーザー管理（管理者） |
| Agent | `/api/agent` | 子ノード間のマシン間認証（ペアリングキーで JWT を取得） |
| System | `/api/system` | CPU、メモリ、ディスク、ネットワーク、負荷、WebSocket リアルタイムストリーム |
| Nodes | `/api/nodes` | マルチノード管理と現在の管理ホストの切り替え |
| Sites | `/api/sites` | Web サイト仮想ホスト管理（Nginx / OpenResty / Apache） |
| WAF | `/api/waf` | Web アプリケーションファイアウォールのルールとサイト保護 |
| Databases | `/api/databases` | MySQL / MariaDB / Redis / PostgreSQL / MongoDB の接続とクエリ |
| Docker | `/api/docker` | コンテナ、イメージ、ボリューム、コンテナ編集 |
| Files | `/api/files` | ファイル参照、転送、権限、圧縮/解凍 |
| Recycle | `/api/recycle` | ごみ箱（復元、自動クリーンアップ） |
| Terminal | `/api/terminal` | WebSocket ターミナルセッション（`?token=` で認証） |
| App Store | `/api/appstore` | アプリストアのレシピのインストールと管理 |
| Cron / Firewall / SSL | `/api/cron`、`/api/firewall`、`/api/ssl` | スケジュールタスク / ファイアウォール / 証明書 |
| Plugins | `/api/plugins`、`/api/op` | プラグイン管理とプラグインオープンインターフェース（GPOP） |

完全なルート一覧と認証レベル（`PROTECTED` / `ADMIN` / エンドポイント内自己認証）は [AGENTS.md](../AGENTS.md) の第 3 節を参照してください。API ドキュメントはデフォルトで無効です。デバッグ時は環境変数 `GRAW_ENABLE_DOCS=1` を設定してから `/docs` にアクセスしてください。

## デフォルトアカウント

初回起動時に `backend/data/users.json` へ自動でシードされます：

- アカウント: `admin`
- パスワード: `admin123`
- 状態: 初回ログイン後にパスワード変更が強制されます

署名キーは `backend/data/secret.key` に永続化されます（初回起動時に自動生成）。本番環境ではこのファイルと `users.json` を安全に保管し、デフォルトパスワードを変更してください。

## パスワードリセット

管理者パスワードを忘れた、または Web パネルにログインできない場合は、サーバーのローカルで CLI スクリプトを直接実行してリセットできます（バックエンドサービスの起動は不要）：

```bash
cd backend

# すべてのアカウントを一覧表示
python reset_password.py --list

# 指定アカウントをリセット（対話的に新パスワードを入力）
python reset_password.py admin

# アカウントを指定しない場合、スクリプトが選択を促します
python reset_password.py
```

スクリプトは `backend/data/users.json` を直接読み書きし、パスワード入力は非表示になり、リセット後に「初回ログイン時にパスワード変更必須」フラグを自動的にクリアします。新しいパスワードは 6 文字以上必要です。

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

よく使う環境変数：

| 変数 | 説明 |
|------|------|
| `HOST_ROOT` | コンテナ内におけるホストルートディレクトリのマウントポイント（例：`/host`）。ホストモードを有効化します |
| `GRAW_HOST_DATA` | パネルの `data` ディレクトリのホスト上の実際のパス。アプリストアのインストールに必要 |
| `GRAW_ENABLE_DOCS` | `1` に設定すると `/docs`、`/redoc`、`/openapi.json` を公開します |
| `GRAW_SESSION_ONLINE_SECONDS` | セッションのオンライン判定に使うアイドルしきい値（秒）。デフォルトは 2 時間 |
| `TZ` | コンテナのタイムゾーン。例：`Asia/Shanghai` |

## プロジェクトドキュメント

- [AGENTS.md](../AGENTS.md) —— アーキテクチャ、規約、よくある落とし穴（**コードを変更する前に必ず通読してください**）
- [CONTRIBUTING.md](../CONTRIBUTING.md) —— コントリビューションガイドと貢献者ライセンス同意書（CLA）
- [SECURITY.md](../SECURITY.md) —— セキュリティ問題の報告フロー
- [CHANGELOG.md](../CHANGELOG.md) —— バージョン変更履歴
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) —— プラグインオープンプロトコル（GPOP）
- [app-store/](../app-store/) —— アプリストアのレシピ（YAML）
- [plugin-examples/](../plugin-examples/) —— プラグインのサンプル

## 貢献

Issue や Pull Request を歓迎します。詳しくは [CONTRIBUTING.md](../CONTRIBUTING.md) をご覧ください。

> **貢献者ライセンス同意書（CLA）**：本プロジェクトにコード、ドキュメント、その他のコンテンツを提出した時点で、あなたは [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla) に**同意したものとみなされます**。これには、**WuHuLaB** があなたの貢献を**商業目的**で利用し、**クローズドソース（プロプライエタリ）ライセンス**で再許諾/配布する権利を保持することが含まれます。

## 寄付

Graw がお役に立てば、ぜひ作者にコーヒーをご馳走してください ☕

- 愛発電（Afdian）：<https://afdian.com/a/shunianssy>
- 雨云（スポンサー、格安サーバー）：<https://www.rainyun.com/NjQwNjg5_>

## License

本プロジェクトは [AGPLv3](../LICENSE) の下でオープンソースとして公開されています。

[貢献者ライセンス同意書（CLA）](../CONTRIBUTING.md#7-贡献者许可协议cla) に基づき、WuHuLaB は本プロジェクト（コミュニティの貢献を含む）を**商業目的**で利用し、**クローズドソース（プロプライエタリ）ライセンス**で再許諾または配布する権利を保持します。