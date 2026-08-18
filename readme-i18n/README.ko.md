# Graw

운영체제 데스크톱 스타일의 상호작용 디자인을 갖춘 웹 기반 서버 관리 패널입니다. 프론트엔드는 Vue 3 + Vite, 백엔드는 FastAPI를 사용하며 실시간 시스템 모니터링, Docker 관리, 프로세스 관리, 파일 관리, 웹 터미널, 메모 등의 기능을 제공합니다.

## 설치 방법

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

`8041`을 원하는 포트로 바꾸세요. Graw는 Docker에서 통합 래퍼를 사용하므로 포트 하나만 있으면 사용할 수 있으며 여러 레코드를 설정할 필요가 없습니다.

## 기능

- **계정·권한 시스템** — JWT 기반 로그인, 역할(관리자/일반 사용자), 계정 관리, 가장 빈 비밀번호 변경. 로그인 후 보호되는 모든 API는 `Authorization: Bearer <token>`이 필요함
- **데스크톱 스타일 UI** — 창형 앱, 작업 표시줄, 바탕 화면 바로 가기, 드래그, 최대화/최소화 지원
- **실시간 시스템 모니터링** — CPU, 메모리, 디스크, 네트워크, 부하를 WebSocket으로 실시간 그래프 표시
- **웹사이트 관리** — Nginx / Apache 가상 호스트의 생성·삭제·시작·중지·설정 생성 및 조회
- **데이터베이스 관리** — MySQL / MariaDB / Redis 연결 관리, DB/테이블 탐색, SQL / Redis 명령 실행
- **예약 작업** — Cron 표현식 관리(Linux crontab / Windows schtasks 래퍼)
- **방화벽** — 포트 규칙과 IP 허용/거부 목록 관리(iptables / netsh)
- **SSL 인증서** — 사용자 지정 인증서 업로드 및 Let's Encrypt 발급(certbot)
- **로그 센터** — 시스템·웹사이트·패널 로그의 실시간 조회 및 삭제
- **Docker 관리** — 컨테이너·이미지 조회, 시작, 중지, 로그 보기 등
- **프로세스 관리** — 실행 중인 프로세스 목록과 세부 정보 조회
- **파일 관리** — 디렉터리 탐색, 업로드/다운로드, 권한 변경, 압축/해제, 복사/이름 변경
- **웹 터미널** — xterm.js 기반 브라우저 내 터미널로 서버를 직접 조작(WebSocket은 `?token=`으로 인증)
- **메모** — 시스템 메모 기록 및 조회

## 기술 스택

| 계층 | 기술 |
|------|------|
| 프론트엔드 | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| 백엔드 | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| 통신 | REST API + WebSocket |

## 디렉터리 구조

```
Graw/
├── frontend/          # Vue 3 프론트엔드
│   ├── src/
│   │   ├── components/     # 데스크톱, 창, 작업 표시줄, 카드 컴포넌트
│   │   ├── api.js          # 백엔드 API 래퍼
│   │   └── App.vue         # 루트 컴포넌트(데스크톱)
│   ├── package.json
│   └── vite.config.js
├── backend/           # FastAPI 백엔드
│   ├── app/
│   │   ├── main.py         # 애플리케이션 진입점
│   │   └── routers/        # 모듈별 라우터(system, docker, process, files, terminal, notes)
│   ├── api/                # 구버전 호환 라우터(직접 참조 가능)
│   └── requirements.txt
├── start.bat          # Windows 원클릭 시작
├── start.sh           # Linux / macOS 원클릭 시작
└── README.md
```

## 빠른 시작

### 요구 사항

- Python 3.8+
- Node.js 16+
- (선택) Docker 엔진(Docker 관리 기능에 필요)

### 수동 시작

**1. 백엔드 시작**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # 최초 1회
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 또는 개발용으로:
py start.py

```

**2. 프론트엔드 시작**

```bash
cd frontend
pnpm install
pnpm run dev
```

### 프로덕션 빌드

프론트엔드 프로덕션 빌드는 `frontend/dist`에 출력되며, 백엔드가 이 디렉터리를 정적 리소스로 자동 감지해 마운트합니다:

```bash
cd frontend
npm run build
```

그런 다음 백엔드를 시작하면 `http://localhost:8000`에서 전체 앱에 접속할 수 있습니다.

## API 개요

| 모듈 | 접두사 | 설명 |
|------|------|------|
| Auth | `/api/auth` | 로그인, 현재 사용자, 비밀번호 변경, 사용자 관리(관리자) |
| System | `/api/system` | CPU, 메모리, 디스크, 네트워크, 부하, WebSocket 실시간 스트림 |
| Sites | `/api/sites` | 가상 호스트 관리(Nginx/Apache) |
| Databases | `/api/databases` | MySQL/MariaDB/Redis 연결 및 쿼리 관리 |
| Cron | `/api/cron` | 예약 작업(Linux crontab / Windows schtasks) |
| Firewall | `/api/firewall` | 포트 및 IP 방화벽 규칙 |
| SSL | `/api/ssl` | 사용자 지정 인증서 업로드 및 Let's Encrypt 발급 |
| Logs | `/api/logs` | 로그 조회 및 삭제 |
| Docker | `/api/docker` | 컨테이너·이미지 관리 |
| Process | `/api/process` | 프로세스 목록과 세부 정보 |
| Files | `/api/files` | 파일 탐색, 전송, 권한, 압축/해제 |
| Terminal | `/api/terminal` | WebSocket 터미널 세션(`?token=` 인증) |
| Notes | `/api/notes` | 메모 CRUD |

`/api/auth/login`과 `/api/health`를 제외한 모든 엔드포인트는 `Authorization: Bearer <token>` 헤더를 요구합니다.

## 기본 계정

최초 시작 시 `backend/data/users.json`에 자동으로 시드됩니다:

- 계정명: `admin`
- 비밀번호: `admin123`
- 상태: 최초 로그인 후 비밀번호 변경 강제

서명 키는 `backend/data/secret.key`에 저장됩니다(최초 시작 시 자동 생성). 프로덕션에서는 이 파일과 `users.json`을 안전하게 보관하고 기본 비밀번호를 변경하세요.

자세한 API 정의는 `backend/app/routers/` 아래의 라우터 파일을 참고하세요.

## 비밀번호 재설정

관리자 비밀번호를 잊었거나 웹 패널에 로그인할 수 없다면, 서버 로컬에서 CLI 스크립트를 직접 실행해 비밀번호를 재설정할 수 있습니다(백엔드 서비스를 시작할 필요 없음):

```bash
cd backend

# 모든 계정 나열
python reset_password.py --list

# 지정 계정 재설정(대화형으로 새 비밀번호 입력)
python reset_password.py admin

# 계정을 지정하지 않으면 스크립트가 선택을 요청합니다
python reset_password.py
```

이 스크립트는 `backend/data/users.json`을 직접 읽고 쓰며, 비밀번호 입력은 숨겨지고 재설정 후 "첫 로그인 시 비밀번호 변경 필수" 플래그를 자동으로 지웁니다. 새 비밀번호는 6자 이상이어야 합니다.

## 설정

프론트엔드 개발 서버의 프록시 설정은 `frontend/vite.config.js`에 있으며, 기본적으로 `/api`와 WebSocket을 `http://localhost:8000`으로 전달합니다:

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

## 기여

Issue나 Pull Request를 환영합니다.

## License

AGPLv3