# Graw

Ein webbasiertes Serververwaltungspanel mit einem Interaktionsdesign „wie ein Desktop-Betriebssystem" (Fenster, Taskleiste, Desktop-Verknüpfungen), das zusätzlich einen Standard-Panelmodus im 1Panel-Stil bietet. Das Frontend nutzt Vue 3 + Vite, das Backend FastAPI.

Über den lokalen Rechner hinaus kann Graw weitere Hosts über einen **Agent-Tunnel + gepaarte Zugriffsschlüssel** als „untergeordnete Knoten" in ein einheitliches Panel einbinden: An einer Stelle den Host wechseln und die Container, Websites, Dateien, Terminals und Firewalls mehrerer Server verwalten.

### Mehrsprachige README

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


## Nützliche Links

| Projekt | Adresse |
|------|------|
| Quellcode-Repository | <https://github.com/wuhulab/Graw> |
| App-Store-Rezepte | <https://github.com/wuhulab/Graw-app-store> |
| Docker-Image | <https://hub.docker.com/r/shunx/graw> |
| Offizielle Website | <https://graw.shunx.top/> |
| Problem melden | <https://github.com/wuhulab/Graw/issues> |
| Mit einer Spende unterstützen | <https://afdian.com/a/shunianssy> |

## Wie wird es installiert?

Graw läuft als Container, aber **alle seine Verwaltungsvorgänge (Docker-Container/-Images, Installation aus dem App-Store, Web-Terminal, Prozesse/Firewall, Website-Konfiguration usw.) müssen auf dem Host-Rechner wirken**. Daher **darf** man **nicht** einfach einen nackten Container etwa mit `-p Port:8000` starten, sondern muss ihn im unten beschriebenen „vollständigen Host-Modus" ausführen: Der Container muss auf das Docker des Hosts (Socket) und das Wurzelverzeichnis des Hosts (`/host`) zugreifen können und über Rechte auf Host-Ebene verfügen (`privileged` + `pid host`).

**Option 1: Linux-Server (empfohlen, nutzt das host-Netzwerk und lauscht direkt auf Port 8000 des Hosts)**

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

Nach dem Start ist das Panel unter `http://<Server-IP>:8000` erreichbar.

**Option 2: Bridge-Netzwerk (benutzerdefinierter Zugriffsport, z. B. 8041)**

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

Nach dem Start ist das Panel unter `http://<Server-IP>:8041` erreichbar.

**Option 3: Docker Compose (eine Orchestrierung mit hohen Rechten ist bereits im Repository enthalten)**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

Bedeutung der einzelnen Parameter (erforderlich, damit das Panel den Host vollständig verwalten kann):

- `--privileged`: verleiht dem Container alle Kernel-Fähigkeiten; andernfalls können Vorgänge wie `chroot /host`, iptables/Firewall und Mounts im Container nicht wirksam werden.
- `--pid host`: teilt den Prozess-Namespace des Hosts, sodass Prozessverwaltung und Systemüberwachung alle Host-Prozesse sehen.
- `--network host`: nutzt das Netzwerk des Hosts, sodass das Panel direkt auf Port 8000 des Hosts lauscht (Option 2 verwendet stattdessen Port-Mapping über `-p`).
- `-v /:/host:rslave` + `HOST_ROOT=/host`: bindet das Wurzelverzeichnis des Hosts unter `/host` in den Container ein; so kann das Panel über `chroot /host` Host-Dateien und -Befehle (nginx/certbot/crontab usw.) bearbeiten.
- `-v /var/run/docker.sock:/var/run/docker.sock`: verbindet sich mit der Docker-Engine des Hosts (Verwaltung von Containern/Images/Logs).
- `/opt/graw/data` ist das Datenverzeichnis des Panels (an den Host gebunden); `GRAW_HOST_DATA=/opt/graw/data` teilt dem Host mit, wo die docker-compose-Datei liegt, was der Docker-App-Store für eine erfolgreiche Installation benötigt.

> ⚠️ **Sicherheitswarnung**: Der obige Container besitzt faktisch Betriebsfähigkeiten auf Root-Ebene des Hosts und sollte daher nur in einer vertrauenswürdigen Umgebung eingesetzt werden. Bitte **ändern Sie nach der ersten Anmeldung sofort das Standardpasswort** und schützen Sie die Anmeldedateien unter `backend/data/` sorgfältig.

## Funktionen

- **Konten- und Berechtigungssystem** — JWT-basierte Benutzeranmeldung, Rollen (Administrator / normaler Benutzer), Kontoverwaltung, erzwungene Passwortänderung, Anmeldeprotokoll, Verwaltung der Online-Sitzungen (Online-Status wird anhand der Token-Gültigkeit und der letzten Aktivitätszeit bestimmt)
- **Zwei Interface-Formen** — Desktop-ähnlicher Modus (Fenster / Taskleiste / Desktop-Verknüpfungen, mit Ziehen, Maximieren/Minimieren) und Standard-Panelmodus im 1Panel-Stil (gruppiertes Seitenleistenmenü + mehrere Tabs)
- **Multi-Knoten-Verwaltung** — das Hauptpanel bindet untergeordnete Knoten über einen Agent-Tunnel + gepaarte Zugriffsschlüssel ein, mit SSH-Schlüsselverteilung, Host-Wechsel auf Anforderungsebene und Fähigkeits-Gating für entfernte untergeordnete Knoten
- **Echtzeit-Systemüberwachung** — CPU, Speicher, Festplatte, Netzwerk und Last, in Echtzeit per WebSocket mit Daten und Diagrammen übertragen, plus Abfragen historischer Metriken
- **Website-Verwaltung** — CRUD für virtuelle Hosts von Nginx / OpenResty / Apache, Start/Stopp, Generieren und Anzeigen der Konfiguration; Koexistenz mit 1Panel/OpenResty und automatische Erkennung externer Websites
- **WAF und Website-Erweiterungen** — Web Application Firewall (WAF), Rewrite-Regeln, Cache- und Website-Erweiterungskonfiguration, Website-Statistiken
- **Datenbankverwaltung** — Verwaltung der Verbindungen zu MySQL / MariaDB / Redis / PostgreSQL / MongoDB, Durchsuchen von Datenbanken/Tabellen, Ausführen von SQL-/Redis-Befehlen, Analyse langsamer Abfragen
- **Docker-Verwaltung** — Anzeigen, Starten, Stoppen, Logs und Ressourcenstatistiken von Containern und Images; kompatibel mit den Ausgabeformaten von docker und podman
- **App-Store** — Installation von Anwendungen per Ein-Klick aus YAML-Rezepten (die Installation erfolgt per `docker compose`), mit eigener Compose-Bearbeitung und Installationsprotokollen
- **Dateiverwaltung** — Verzeichnisse durchsuchen, hoch-/herunterladen, Rechte ändern, komprimieren/entpacken, kopieren/umbenennen; Zwischenablage im Windows-Stil (Ctrl+C/V/Delete) und Hochladen von Ordnern per Drag & Drop
- **Papierkorb** — versehentlich gelöschte Dateien landen im Papierkorb, mit Wiederherstellung und automatischer täglicher Bereinigung (knotenübergreifend)
- **Web-Terminal** — Terminal im Browser auf Basis von xterm.js zur direkten Steuerung des Servers (WebSocket-Authentifizierung über `?token=`)
- **Geplante Aufgaben / Firewall / SSL** — Verwaltung von Cron-Ausdrücken (crontab / schtasks), Port- und IP-Allow-/Deny-Listen (iptables / netsh, einschließlich Steuerung eingehender/ausgehender Verbindungen für von Docker veröffentlichte Ports), Zertifikat-Upload und Let's-Encrypt-Beantragung
- **Sicherheit und Betrieb** — ShunX-Sicherheitseinstieg, Schutz vor Manipulation von Webseiten (Echtzeit-WebSocket-Alarme), einheitliche Firewall-Regeln, Gesundheitscheck, Panel-Backup, Dienstüberwachung, Erkennung ablaufender Zertifikate, Benachrichtigungszentrum
- **Sonstiges** — Log-Center, Prozessverwaltung, Notizen, Intranet-Penetration (Frp), Netzwerkspeicher, FTP-Benutzer, PHP-Versionsverwaltung, Toolbox, offenes Plugin-Protokoll (GPOP)

## Technologischer Stack

| Ebene | Technologie |
|------|------|
| Frontend | Vue 3 (Composition API), Vite 5, Axios, ECharts / vue-echarts, xterm.js, vue-i18n |
| Backend | Python 3.11, FastAPI 0.115, Uvicorn, Pydantic 2, psutil, docker SDK |
| Kommunikation | REST API (`/api/*`) + WebSocket (Überwachungsstream, Terminal) |
| Deployment | Mehrstufiger Docker-Build (Node-Build des Frontends → Python-Laufzeit des Backends) |

## Verzeichnisstruktur

```
Graw/
├── frontend/                 # Vue-3-Frontend
│   ├── src/
│   │   ├── components/       # Komponenten für Desktop, Fenster, Taskleiste, Karten
│   │   │   └── windows/      # eine unabhängige Fensterkomponente pro Funktion (*Window.vue)
│   │   ├── store/            # reaktiver Singleton-Zustand (auth / systemMetrics / docker ...)
│   │   ├── locales/          # vue-i18n-Locales (insgesamt 22)
│   │   └── App.vue           # Root-Komponente (Umschalten Desktop-Umgebung / Panelmodus)
│   ├── vite.config.js        # Dev-Proxy /api (inkl. ws) → :8000
│   └── package.json
├── backend/                  # FastAPI-Backend
│   ├── app/
│   │   ├── main.py           # Anwendungseinstieg: Routenregistrierung, Middleware, Lifespan-Hintergrundaufgaben
│   │   ├── auth.py           # JWT-Auth-Abhängigkeiten und Benutzer-Seeding
│   │   ├── agent_*.py        # Authentifizierung / Konfiguration / Tunnel-Proxy des Agenten untergeordneter Knoten
│   │   ├── node_manager.py   # Multi-Knoten-Kontext und Host-Wechsel auf Anforderungsebene
│   │   ├── hostfs.py         # Adapterschicht für das Dateisystem des Hosts (chroot /host)
│   │   ├── routers/          # Geschäftsrouter je Modul
│   │   └── data/             # Laufzeitdaten (gitignore, eingeschränkte Rechte)
│   ├── test_*_unit.py        # pytest-Unit-Tests (test_*_e2e.py sind End-to-End-Fälle)
│   └── requirements.txt
├── app-store/                # YAML-Rezepte und Icons des Community-App-Stores
├── plugin-examples/          # Beispiele für das offene Plugin-Protokoll (GPOP)
├── readme-i18n/              # mehrsprachige READMEs
├── docs/                     # ergänzende Dokumentation (Plugin-Protokoll usw.)
├── agent/                    # Ressourcen und Skills zum Agenten untergeordneter Knoten
├── Dockerfile                # mehrstufiger Build (Frontend-Build → Backend-Laufzeit)
├── docker-compose.yml        # Orchestrierung mit hohen Rechten für „vollständige Host-Verwaltung"
├── start.sh / start.bat      # Ein-Klick-Start für die lokale Entwicklung (Backend + Frontend)
└── AGENTS.md                 # Architektur und Entwicklungsrichtlinien der Codebasis (vor Codeänderungen lesen)
```

## Schnellstart

### Anforderungen

- Python 3.8+ (Produktionsimage ist 3.11)
- Node.js 16+
- (Optional) Docker-Engine, für die Docker-Verwaltungsfunktion

### Ein-Klick-Start (Entwicklung)

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

Das Skript erstellt bei Bedarf eine virtuelle Umgebung, installiert Abhängigkeiten und startet Backend und Frontend gleichzeitig.

### Manueller Start

**1. Backend starten**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # nur beim ersten Mal
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# oder das Entwicklungs-Startskript verwenden
python start.py
```

**2. Frontend starten**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173, Vite leitet /api und ws an :8000 weiter
```

> Unter Windows unterstützen PowerShell/cmd kein `&&`; trennen Sie mehrere Befehle durch ein Semikolon `;`.

### Produktions-Build

Der Produktions-Build des Frontends wird nach `frontend/dist` geschrieben; das Backend erkennt dieses Verzeichnis automatisch und bindet es als statische Ressourcen ein:

```bash
cd frontend
npm run build
```

Danach starten Sie einfach das Backend und erreichen die vollständige Anwendung unter `http://localhost:8000` (das Frontend wird vom Backend aus derselben Origin ausgeliefert, ohne CORS-Konfiguration). Sie können das Image auch direkt mit der im Repository enthaltenen `Dockerfile` / `docker-compose.yml` bauen und einsetzen.

## API-Überblick

Alle Endpunkte haben das Präfix `/api/*`; außer `/api/auth/login` und `/api/health` erfordern alle den Header `Authorization: Bearer <token>`.

| Modul | Präfix | Beschreibung |
|------|------|------|
| Auth | `/api/auth` | Anmeldung, aktueller Benutzer, Passwortänderung, Benutzerverwaltung (Admin) |
| Agent | `/api/agent` | Maschine-zu-Maschine-Authentifizierung untergeordneter Knoten (gepaarter Schlüssel gegen JWT) |
| System | `/api/system` | CPU, Speicher, Festplatte, Netzwerk, Last, WebSocket-Echtzeitstream |
| Nodes | `/api/nodes` | Multi-Knoten-Verwaltung und Wechsel des aktuell verwalteten Hosts |
| Sites | `/api/sites` | Verwaltung virtueller Hosts von Websites (Nginx / OpenResty / Apache) |
| WAF | `/api/waf` | Regeln der Web Application Firewall und Website-Schutz |
| Databases | `/api/databases` | Verbindungen und Abfragen für MySQL / MariaDB / Redis / PostgreSQL / MongoDB |
| Docker | `/api/docker` | Container, Images, Volumes und Container-Bearbeitung |
| Files | `/api/files` | Dateibrowsing, Übertragung, Rechte, Komprimieren/Entpacken |
| Recycle | `/api/recycle` | Papierkorb (Wiederherstellung, automatische Bereinigung) |
| Terminal | `/api/terminal` | WebSocket-Terminalsitzungen (Authentifizierung über `?token=`) |
| App Store | `/api/appstore` | Installation und Verwaltung von App-Store-Rezepten |
| Cron / Firewall / SSL | `/api/cron`, `/api/firewall`, `/api/ssl` | geplante Aufgaben / Firewall / Zertifikate |
| Plugins | `/api/plugins`, `/api/op` | Plugin-Verwaltung und offenes Plugin-Interface (GPOP) |

Die vollständige Routenliste und die Authentifizierungsstufen (`PROTECTED` / `ADMIN` / Selbstauthentifizierung auf Endpunktebene) finden Sie in Abschnitt 3 von [AGENTS.md](../AGENTS.md). Die API-Dokumentation ist standardmäßig deaktiviert; setzen Sie zum Debuggen die Umgebungsvariable `GRAW_ENABLE_DOCS=1` und rufen Sie dann `/docs` auf.

## Standard-Konto

Beim ersten Start wird automatisch ein Eintrag in `backend/data/users.json` angelegt:

- Konto: `admin`
- Passwort: `admin123`
- Status: erzwungene Passwortänderung nach der ersten Anmeldung

Der Signaturschlüssel wird unter `backend/data/secret.key` gespeichert (wird beim ersten Start automatisch generiert). Bewahren Sie diese Datei und `users.json` in der Produktion sorgfältig auf und ändern Sie das Standardpasswort.

## Passwort zurücksetzen

Wenn Sie das Administratorpasswort vergessen oder sich nicht am Web-Panel anmelden können, können Sie es direkt auf dem Server über ein Kommandozeilenskript zurücksetzen (ohne den Backend-Dienst zu starten):

```bash
cd backend

# Alle Konten auflisten
python reset_password.py --list

# Ein bestimmtes Konto zurücksetzen (neues Passwort interaktiv eingeben)
python reset_password.py admin

# Ohne Kontoname fordert das Skript zur Auswahl auf
python reset_password.py
```

Das Skript liest und schreibt direkt `backend/data/users.json`, verbirgt die Passworteingabe und entfernt nach dem Zurücksetzen automatisch das Flag „Passwort muss beim ersten Login geändert werden". Das neue Passwort muss mindestens 6 Zeichen lang sein.

## Konfiguration

Die Proxy-Konfiguration des Frontend-Dev-Servers liegt in `frontend/vite.config.js`; standardmäßig leitet sie `/api` und WebSocket an `http://localhost:8000` weiter:

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

Häufig verwendete Umgebungsvariablen:

| Variable | Beschreibung |
|------|------|
| `HOST_ROOT` | Einhängepunkt des Wurzelverzeichnisses des Hosts im Container (z. B. `/host`), aktiviert den Host-Modus |
| `GRAW_HOST_DATA` | tatsächlicher Pfad des `data`-Verzeichnisses des Panels auf dem Host, erforderlich für die App-Store-Installation |
| `GRAW_ENABLE_DOCS` | wenn auf `1` gesetzt, werden `/docs`, `/redoc`, `/openapi.json` freigegeben |
| `GRAW_SESSION_ONLINE_SECONDS` | Leerlaufschwelle (Sekunden) zur Bestimmung, ob eine Sitzung online ist; Standard 2 Stunden |
| `TZ` | Zeitzone des Containers, z. B. `Asia/Shanghai` |

## Projektdokumentation

- [AGENTS.md](../AGENTS.md) — Architektur, Konventionen und häufige Fallstricke (**bitte vor Codeänderungen vollständig lesen**)
- [CONTRIBUTING.md](../CONTRIBUTING.md) — Beitragsleitfaden und Contributor License Agreement (CLA)
- [SECURITY.md](../SECURITY.md) — Verfahren zur Meldung von Sicherheitsproblemen
- [CHANGELOG.md](../CHANGELOG.md) — Versionsänderungsprotokoll
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) — offenes Plugin-Protokoll (GPOP)
- [app-store/](../app-store/) — App-Store-Rezepte (YAML)
- [plugin-examples/](../plugin-examples/) — Plugin-Beispiele


## Mitwirken

Issues und Pull Requests sind willkommen; siehe [CONTRIBUTING.md](../CONTRIBUTING.md) für Details.

> **Contributor License Agreement (CLA)**: Mit dem Einreichen von Code, Dokumentation oder anderen Inhalten an dieses Projekt **stimmen Sie standardmäßig dem [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla) zu**, einschließlich der Berechtigung von **WuHuLaB**, das Recht zu behalten, Ihren Beitrag für **kommerzielle Zwecke** zu nutzen und ihn unter einer **Closed-Source-(proprietären) Lizenz** weiterzulizenzieren/zu verbreiten.

## Spenden

Wenn Graw Ihnen geholfen hat, sind Sie herzlich eingeladen, dem Autor einen Kaffee auszugeben ☕

- Afdian (爱发电): <https://afdian.com/a/shunianssy>
- RainYun (雨云, Sponsor, günstige Server): <https://www.rainyun.com/NjQwNjg5_>

## License

Dieses Projekt wird als Open Source unter [AGPLv3](../LICENSE) veröffentlicht.

Gemäß dem [Contributor License Agreement (CLA)](../CONTRIBUTING.md#7-贡献者许可协议cla) behält WuHuLaB das Recht, dieses Projekt (einschließlich Community-Beiträgen) für **kommerzielle Zwecke** zu nutzen und es unter einer **Closed-Source-(proprietären) Lizenz** weiterzulizenzieren oder zu verbreiten.