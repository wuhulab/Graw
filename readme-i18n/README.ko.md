# Graw

운영체제 데스크톱 스타일의 상호작용 디자인(창, 작업 표시줄, 바탕 화면 바로 가기)을 갖춘 웹 기반 서버 관리 패널이며, 1Panel 스타일의 표준 패널 모드도 내장되어 있습니다. 프론트엔드는 Vue 3 + Vite, 백엔드는 FastAPI를 사용합니다.

Graw는 로컬 머신 외에도 **Agent 터널 + 페어링된 액세스 키**를 통해 다른 호스트를 "자식 노드"로 통합 패널 관리에 편입할 수 있습니다. 한 곳에서 호스트를 전환하며 여러 서버의 컨테이너, 웹사이트, 파일, 터미널, 방화벽을 관리할 수 있습니다.

### 다국어 README

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

## 관련 링크

| 프로젝트 | 주소 |
|------|------|
| 소스 저장소 | <https://github.com/wuhulab/Graw> |
| 앱 스토어 레시피 | <https://github.com/wuhulab/Graw-app-store> |
| Docker 이미지 | <https://hub.docker.com/r/shunx/graw> |
| 공식 사이트 | <https://graw.shunx.top/> |
| 문제 제보 | <https://github.com/wuhulab/Graw/issues> |
| 후원 | <https://afdian.com/a/shunianssy> |

## 다운로드 방법

Graw는 컨테이너로 실행되지만, **모든 관리 작업(Docker 컨테이너/이미지, 앱 스토어 설치, 웹 터미널, 프로세스/방화벽, 웹사이트 설정 등)은 호스트 머신에 대해 수행되어야 합니다**. 따라서 `-p 포트:8000`처럼 컨테이너를 그대로 띄우는 것만으로는 **안 되며**, 아래 설명하는 "완전 호스트 모드"로 시작해야 합니다. 컨테이너가 호스트의 Docker(socket)와 호스트 루트 디렉터리(`/host`)에 접근할 수 있어야 하고, 호스트 수준 권한(`privileged` + `pid host`)을 가져야 합니다.

**방법 1: Linux 서버(권장, host 네트워크로 호스트의 8000 포트를 직접 리스닝)**

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

시작 후 `http://<서버 IP>:8000`으로 접속합니다.

**방법 2: Bridge 네트워크(접속 포트 사용자 지정, 예: 8041)**

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

시작 후 `http://<서버 IP>:8041`로 접속합니다.

**방법 3: Docker Compose(저장소에 고권한 오케스트레이션이 이미 포함됨)**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

각 파라미터의 의미(패널이 호스트를 완전히 관리하기 위해 필수):

- `--privileged`: 컨테이너에 모든 커널 권한을 부여합니다. 그렇지 않으면 `chroot /host`, iptables/방화벽, 마운트 등의 작업이 컨테이너 내에서 적용되지 않습니다.
- `--pid host`: 호스트의 프로세스 네임스페이스를 공유하여 프로세스 관리/시스템 모니터링이 호스트의 모든 프로세스를 볼 수 있게 합니다.
- `--network host`: 호스트 네트워크를 사용하여 패널이 호스트의 8000 포트를 직접 리스닝합니다(방법 2는 `-p 포트 매핑`을 사용).
- `-v /:/host:rslave` + `HOST_ROOT=/host`: 호스트 루트 디렉터리를 컨테이너의 `/host`에 마운트하여, 패널이 `chroot /host`를 통해 호스트 파일과 명령(nginx/certbot/crontab 등)을 조작할 수 있게 합니다.
- `-v /var/run/docker.sock:/var/run/docker.sock`: 호스트 Docker 엔진에 연결합니다(컨테이너/이미지/로그 관리).
- `/opt/graw/data`는 패널 데이터 디렉터리(호스트에 바인딩)입니다. `GRAW_HOST_DATA=/opt/graw/data`는 호스트 측 docker-compose 파일 위치를 알려 주며, Docker 앱 스토어가 설치를 완료하는 데 필요합니다.

> ⚠️ **보안 경고**: 위 컨테이너는 사실상 호스트의 root 수준 조작 권한을 가집니다. 신뢰할 수 있는 환경에만 배포하는 것을 권장합니다. 최초 로그인 후 **즉시 기본 비밀번호를 변경**하고, `backend/data/` 아래의 자격 증명 파일을 안전하게 보관하세요.

## 기능

- **계정 및 권한 시스템** —— JWT 기반 사용자 로그인, 역할(관리자/일반 사용자), 계정 관리, 비밀번호 변경 강제, 로그인 로그, 온라인 세션 관리(토큰 유효 기간과 마지막 활동 시간으로 온라인 판정)
- **듀얼 UI 형태** —— 데스크톱 스타일 모드(창/작업 표시줄/바탕 화면 바로 가기, 드래그·최대화/최소화 지원)와 1Panel 스타일 표준 패널 모드(사이드바 그룹 메뉴 + 다중 탭)
- **다중 노드 관리** —— 메인 패널이 Agent 터널 + 페어링된 액세스 키로 자식 노드를 관리. SSH 키 배포, 요청 단위 호스트 전환, 원격 자식 노드 기능 게이팅 지원
- **실시간 시스템 모니터링** —— CPU, 메모리, 디스크, 네트워크, 부하를 WebSocket으로 실시간 데이터와 차트로 푸시. 이력 지표 조회 지원
- **웹사이트 관리** —— Nginx / OpenResty / Apache 가상 호스트 생성·조회·수정·삭제, 시작/중지, 설정 생성과 조회. 1Panel/OpenResty와 공존하며 외부 사이트 자동 검색
- **WAF 및 사이트 강화** —— 웹 애플리케이션 방화벽, rewrite 규칙, 캐시와 사이트 강화 설정, 사이트 통계
- **데이터베이스 관리** —— MySQL / MariaDB / Redis / PostgreSQL / MongoDB 연결 관리, DB/테이블 탐색, SQL / Redis 명령 실행, 슬로우 쿼리 분석
- **Docker 관리** —— 컨테이너와 이미지 조회, 시작, 중지, 로그, 리소스 통계. docker와 podman 출력 형식 모두 지원
- **앱 스토어** —— YAML 레시피로 원클릭 앱 설치(설치는 `docker compose`). 사용자 지정 compose 편집과 설치 로그 지원
- **파일 관리** —— 디렉터리 탐색, 업로드/다운로드, 권한 변경, 압축/해제, 복사/이름 변경. Windows 스타일 클립보드(Ctrl+C/V/Delete)와 폴더 드래그 앤 드롭 업로드
- **휴지통** —— 실수로 삭제한 파일은 휴지통으로 이동하며, 복원과 일일 자동 정리를 지원(노드 간 동작)
- **웹 터미널** —— xterm.js 기반 브라우저 내 터미널로 서버를 직접 조작(WebSocket은 `?token=`으로 인증)
- **예약 작업 / 방화벽 / SSL** —— Cron 표현식 관리(crontab / schtasks), 포트와 IP 허용/차단 목록(iptables / netsh, Docker 공개 포트의 인바운드/아웃바운드 제어 포함), 인증서 업로드와 Let's Encrypt 발급
- **보안 및 운영** —— ShunX 보안 엔트리, 웹 페이지 변조 방지(WS 실시간 알림), 방화벽 규칙 통합, 헬스 체크, 패널 백업, 서비스 모니터링, 인증서 만료 감지, 알림 센터
- **기타** —— 로그 센터, 프로세스 관리, 메모, 인트라넷 터널링(Frp), 네트워크 스토리지, FTP 사용자, PHP 버전 관리, 툴박스, 플러그인 오픈 프로토콜(GPOP)

## 기술 스택

| 계층 | 기술 |
|------|------|
| 프론트엔드 | Vue 3(Composition API), Vite 5, Axios, ECharts / vue-echarts, xterm.js, vue-i18n |
| 백엔드 | Python 3.11, FastAPI 0.115, Uvicorn, Pydantic 2, psutil, docker SDK |
| 통신 | REST API(`/api/*`) + WebSocket(모니터링 스트림, 터미널) |
| 배포 | 멀티 스테이지 Docker 빌드(프론트엔드 Node 빌드 → 백엔드 Python 런타임) |

## 디렉터리 구조

```
Graw/
├── frontend/                 # Vue 3 프론트엔드
│   ├── src/
│   │   ├── components/       # 데스크톱, 창, 작업 표시줄, 카드 컴포넌트
│   │   │   └── windows/      # 기능마다 독립된 창 컴포넌트(*Window.vue)
│   │   ├── store/            # reactive 싱글턴 상태(auth / systemMetrics / docker ...)
│   │   ├── locales/          # vue-i18n 다국어(총 22종)
│   │   └── App.vue           # 루트 컴포넌트(데스크톱 환경 / 패널 모드 전환)
│   ├── vite.config.js        # 개발 프록시 /api(ws 포함) → :8000
│   └── package.json
├── backend/                  # FastAPI 백엔드
│   ├── app/
│   │   ├── main.py           # 애플리케이션 진입점: 라우트 등록, 미들웨어, lifespan 백그라운드 작업
│   │   ├── auth.py           # JWT 인증 의존성과 사용자 시드
│   │   ├── agent_*.py        # 자식 노드 Agent 인증 / 설정 / 터널 프록시
│   │   ├── node_manager.py   # 다중 노드 컨텍스트와 요청 단위 호스트 전환
│   │   ├── hostfs.py         # 호스트 파일 시스템 적응 계층(chroot /host)
│   │   ├── routers/          # 각 업무 모듈 라우터
│   │   └── data/             # 런타임 데이터(gitignore, 권한 강화)
│   ├── test_*_unit.py        # pytest 단위 테스트(test_*_e2e.py는 엔드투엔드 케이스)
│   └── requirements.txt
├── app-store/                # 커뮤니티 앱 스토어 YAML 레시피와 아이콘
├── plugin-examples/          # 플러그인 오픈 프로토콜(GPOP) 예시
├── readme-i18n/              # 다국어 README
├── docs/                     # 보충 문서(플러그인 프로토콜 등)
├── agent/                    # 자식 노드 Agent 관련 리소스와 스킬
├── Dockerfile                # 멀티 스테이지 빌드(프론트엔드 빌드 → 백엔드 런타임)
├── docker-compose.yml        # "완전 호스트 관리" 고권한 오케스트레이션
├── start.sh / start.bat      # 로컬 개발 원클릭 시작(백엔드 + 프론트엔드)
└── AGENTS.md                 # 코드베이스 아키텍처와 개발 규약(코드 변경 전 필독)
```

## 빠른 시작

### 요구 사항

- Python 3.8+(프로덕션 이미지는 3.11)
- Node.js 16+
- (선택) Docker 엔진(Docker 관리 기능에 필요)

### 원클릭 시작(개발)

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

스크립트는 필요할 때 가상 환경을 만들고 의존성을 설치한 뒤 백엔드와 프론트엔드를 동시에 시작합니다.

### 수동 시작

**1. 백엔드 시작**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # 최초 1회
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# 또는 개발용 시작 스크립트 사용
python start.py
```

**2. 프론트엔드 시작**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173, Vite가 /api와 ws를 :8000으로 프록시
```

> Windows의 PowerShell/cmd는 `&&`를 지원하지 않습니다. 여러 명령은 세미콜론 `;`으로 구분하세요.

### 프로덕션 빌드

프론트엔드 프로덕션 빌드는 `frontend/dist`에 출력되며, 백엔드가 해당 디렉터리를 정적 리소스로 자동 감지해 마운트합니다:

```bash
cd frontend
npm run build
```

그런 다음 백엔드를 시작하면 `http://localhost:8000`에서 전체 앱에 접속할 수 있습니다(프론트엔드는 백엔드가 동일 출처로 제공하므로 CORS 설정이 필요 없습니다). 저장소의 `Dockerfile` / `docker-compose.yml`로 이미지를 빌드해 배포할 수도 있습니다.

## API 개요

모든 엔드포인트는 `/api/*` 접두사를 가지며, `/api/auth/login`과 `/api/health`를 제외한 모두가 `Authorization: Bearer <token>` 헤더를 요구합니다.

| 모듈 | 접두사 | 설명 |
|------|------|------|
| Auth | `/api/auth` | 로그인, 현재 사용자, 비밀번호 변경, 사용자 관리(관리자) |
| Agent | `/api/agent` | 자식 노드 간 머신 대 머신 인증(페어링 키로 JWT 발급) |
| System | `/api/system` | CPU, 메모리, 디스크, 네트워크, 부하, WebSocket 실시간 스트림 |
| Nodes | `/api/nodes` | 다중 노드 관리와 현재 관리 호스트 전환 |
| Sites | `/api/sites` | 웹사이트 가상 호스트 관리(Nginx / OpenResty / Apache) |
| WAF | `/api/waf` | 웹 애플리케이션 방화벽 규칙과 사이트 보호 |
| Databases | `/api/databases` | MySQL / MariaDB / Redis / PostgreSQL / MongoDB 연결과 쿼리 |
| Docker | `/api/docker` | 컨테이너, 이미지, 볼륨, 컨테이너 편집 |
| Files | `/api/files` | 파일 탐색, 전송, 권한, 압축/해제 |
| Recycle | `/api/recycle` | 휴지통(복원, 자동 정리) |
| Terminal | `/api/terminal` | WebSocket 터미널 세션(`?token=`으로 인증) |
| App Store | `/api/appstore` | 앱 스토어 레시피 설치와 관리 |
| Cron / Firewall / SSL | `/api/cron`, `/api/firewall`, `/api/ssl` | 예약 작업 / 방화벽 / 인증서 |
| Plugins | `/api/plugins`, `/api/op` | 플러그인 관리와 플러그인 오픈 인터페이스(GPOP) |

전체 라우트 목록과 인증 등급(`PROTECTED` / `ADMIN` / 엔드포인트 내 자체 인증)은 [AGENTS.md](../AGENTS.md) 3절을 참고하세요. API 문서는 기본적으로 비활성화되어 있으며, 디버깅 시 환경 변수 `GRAW_ENABLE_DOCS=1`을 설정한 뒤 `/docs`에 접속하세요.

## 기본 계정

최초 시작 시 `backend/data/users.json`에 자동으로 시드됩니다:

- 계정: `admin`
- 비밀번호: `admin123`
- 상태: 최초 로그인 후 비밀번호 변경 강제

서명 키는 `backend/data/secret.key`에 영속화됩니다(최초 시작 시 자동 생성). 프로덕션에서는 이 파일과 `users.json`을 안전하게 보관하고 기본 비밀번호를 변경하세요.

## 비밀번호 재설정

관리자 비밀번호를 잊었거나 웹 패널에 로그인할 수 없다면, 서버 로컬에서 CLI 스크립트를 직접 실행해 재설정할 수 있습니다(백엔드 서비스를 시작할 필요 없음):

```bash
cd backend

# 모든 계정 나열
python reset_password.py --list

# 지정 계정 재설정(대화형으로 새 비밀번호 입력)
python reset_password.py admin

# 계정을 지정하지 않으면 스크립트가 선택을 요청합니다
python reset_password.py
```

스크립트는 `backend/data/users.json`을 직접 읽고 쓰며, 비밀번호 입력은 숨겨지고 재설정 후 "최초 로그인 시 비밀번호 변경 필수" 플래그를 자동으로 지웁니다. 새 비밀번호는 6자 이상이어야 합니다.

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

자주 쓰는 환경 변수:

| 변수 | 설명 |
|------|------|
| `HOST_ROOT` | 컨테이너 내 호스트 루트 디렉터리의 마운트 지점(예: `/host`). 호스트 모드를 활성화합니다 |
| `GRAW_HOST_DATA` | 패널 `data` 디렉터리의 호스트상 실제 경로. 앱 스토어 설치에 필요 |
| `GRAW_ENABLE_DOCS` | `1`로 설정하면 `/docs`, `/redoc`, `/openapi.json`을 공개합니다 |
| `GRAW_SESSION_ONLINE_SECONDS` | 세션 온라인 판정 유휴 임계값(초), 기본 2시간 |
| `TZ` | 컨테이너 시간대, 예: `Asia/Shanghai` |

## 프로젝트 문서

- [AGENTS.md](../AGENTS.md) —— 아키텍처, 규약, 흔한 함정(**코드를 변경하기 전에 반드시 통독하세요**)
- [CONTRIBUTING.md](../CONTRIBUTING.md) —— 기여 가이드와 기여자 라이선스 동의서(CLA)
- [SECURITY.md](../SECURITY.md) —— 보안 문제 보고 절차
- [CHANGELOG.md](../CHANGELOG.md) —— 버전 변경 기록
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) —— 플러그인 오픈 프로토콜(GPOP)
- [app-store/](../app-store/) —— 앱 스토어 레시피(YAML)
- [plugin-examples/](../plugin-examples/) —— 플러그인 예시

## 기여

Issue나 Pull Request를 환영합니다. 자세한 내용은 [CONTRIBUTING.md](../CONTRIBUTING.md)를 참고하세요.

> **기여자 라이선스 동의서(CLA)**: 본 프로젝트에 코드, 문서 또는 기타 콘텐츠를 제출하면 [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla)에 **동의한 것으로 간주됩니다**. 여기에는 **WuHuLaB**가 귀하의 기여를 **상업적 목적**으로 사용하고 **클로즈드 소스(독점) 라이선스**로 재라이선스/배포할 권리를 보유하는 것이 포함됩니다.

## 후원

Graw가 도움이 되셨다면, 저자에게 커피 한 잔 사주세요 ☕

- 爱发电(Afdian): <https://afdian.com/a/shunianssy>
- 雨云(스폰서, 저렴한 서버): <https://www.rainyun.com/NjQwNjg5_>

## License

본 프로젝트는 [AGPLv3](../LICENSE)로 오픈 소스 공개되었습니다.

[기여자 라이선스 동의서(CLA)](../CONTRIBUTING.md#7-贡献者许可协议cla)에 따라 WuHuLaB는 본 프로젝트(커뮤니티 기여 포함)를 **상업적 목적**으로 사용하고 **클로즈드 소스(독점) 라이선스**로 재라이선스 또는 배포할 권리를 보유합니다.