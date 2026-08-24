# Graw

Ein webbasiertes Serververwaltungspanel mit einem Desktop-OS-ähnlichen Interfacedesign. Das Frontend nutzt Vue 3 + Vite; das Backend nutzt FastAPI. Es bietet Echtzeit-Systemüberwachung, Docker-Verwaltung, Prozess- und Dateiverwaltung, ein Web-Terminal und Notizen.

## Wie wird es installiert?

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

Ersetze `8041` durch deinen Port. Graw verwendet in Docker eine einheitliche Kapselung: Es genügt ein einziger Port, mehrere Einträge sind nicht erforderlich.

## Funktionen

- **Konten- und Berechtigungssystem** — JWT-basierte Anmeldung, Rollen (Administrator / normaler Benutzer), Kontenverwaltung, erzwungene Passwortänderung. Alle geschützten APIs erfordern nach der Anmeldung `Authorization: Bearer <token>`
- **Desktop-ähnliche Oberfläche** — Fenster-Anwendungen, Taskleiste, Desktop-Verknüpfungen, Drag & Drop, Maximieren/Minimieren
- **Systemüberwachung in Echtzeit** — CPU, Speicher, Festplatte, Netzwerk und Last werden per WebSocket in Echtzeit mit Diagrammen übertragen
- **Website-Verwaltung** — CRUD für Nginx-/Apache-Virtual-Hosts, Start/Stopp, Generieren und Anzeigen der Konfiguration
- **Datenbankverwaltung** — Verbindungen zu MySQL / MariaDB / Redis, Durchsuchen von DB/Tabellen, Ausführen von SQL-/Redis-Befehlen
- **Geplante Aufgaben** — Verwaltung von Cron-Ausdrücken (Wrapper über Linux crontab / Windows schtasks)
- **Firewall** — Portregeln und IP-Zulassungs-/Sperrlisten (iptables / netsh)
- **SSL-Zertifikate** — Hochladen eigener Zertifikate und Beantragen von Let's Encrypt (certbot)
- **Log-Center** — Anzeigen und Leeren der System-, Website- und Panel-Protokolle
- **Docker-Verwaltung** — Container und Images anzeigen, starten, stoppen, Logs ansehen usw.
- **Prozessverwaltung** — laufende Prozesse und ihre Details anzeigen
- **Dateiverwaltung** — Verzeichnisse durchsuchen, hoch-/herunterladen, Rechte ändern, komprimieren/extrahieren, kopieren und umbenennen
- **Web-Terminal** — ein Terminal im Browser auf Basis von xterm.js zur direkten Steuerung des Servers (WebSocket-Authentifizierung über `?token=`)
- **Notizen** — Systemnotizen notieren und anzeigen

## Technologischer Stack

| Ebene | Technologie |
|------|------|
| Frontend | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| Backend | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| Kommunikation | REST API + WebSocket |

## Verzeichnisstruktur

```
Graw/
├── frontend/          # Vue-3-Frontend
│   ├── src/
│   │   ├── components/     # Komponenten für Desktop, Fenster, Taskleiste, Karten
│   │   ├── api.js          # Wrapper für die Backend-API
│   │   └── App.vue         # Root-Komponente (Desktop)
│   ├── package.json
│   └── vite.config.js
├── backend/           # FastAPI-Backend
│   ├── app/
│   │   ├── main.py         # Anwendungseinstieg
│   │   └── routers/        # Routen pro Modul (system, docker, process, files, terminal, notes)
│   ├── api/                # mit alten Versionen kompatible Routen (direkt verwendbar)
│   └── requirements.txt
├── start.bat          # Ein-Klick-Start unter Windows
├── start.sh           # Ein-Klick-Start unter Linux / macOS
└── README.md
```

## Schnellstart

### Anforderungen

- Python 3.8+
- Node.js 16+
- (Optional) Docker-Engine, benötigt für die Docker-Verwaltung

### Manueller Start

**1. Backend starten**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # beim ersten Mal
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Oder, falls du entwickelst:
py start.py

```

**2. Frontend starten**

```bash
cd frontend
pnpm install
pnpm run dev
```

### Produktions-Build

Der Produktions-Build des Frontends wird nach `frontend/dist` geschrieben; das Backend erkennt dieses Verzeichnis automatisch und bindet es als statische Ressourcen ein:

```bash
cd frontend
npm run build
```

Danach startest du einfach das Backend und erreichst die vollständige Anwendung unter `http://localhost:8000`.

## API-Überblick

| Modul | Präfix | Beschreibung |
|------|------|------|
| Auth | `/api/auth` | Anmeldung, aktueller Benutzer, Passwortänderung, Benutzerverwaltung (Admin) |
| System | `/api/system` | CPU, Speicher, Festplatte, Netzwerk, Last, WebSocket-Echtzeitstream |
| Sites | `/api/sites` | Verwaltung virtueller Hosts (Nginx/Apache) |
| Databases | `/api/databases` | Verwaltung von MySQL/MariaDB/Redis-Verbindungen und Abfragen |
| Cron | `/api/cron` | geplante Aufgaben (Linux crontab / Windows schtasks) |
| Firewall | `/api/firewall` | Firewall-Regeln für Ports und IPs |
| SSL | `/api/ssl` | eigene Zertifikate hochladen und Let's Encrypt beantragen |
| Logs | `/api/logs` | Protokolle anzeigen und leeren |
| Docker | `/api/docker` | Container- und Image-Verwaltung |
| Process | `/api/process` | Prozessliste und Details |
| Files | `/api/files` | Dateibrowsing, Übertragung, Rechte, Komprimieren/Extrahieren |
| Terminal | `/api/terminal` | WebSocket-Terminalsitzungen (Auth über `?token=`) |
| Notes | `/api/notes` | CRUD der Notizen |

Außer `/api/auth/login` und `/api/health` erfordern alle Endpunkte den Header `Authorization: Bearer <token>`.

## Standard-Konto

Beim ersten Start wird automatisch ein Eintrag in `backend/data/users.json` angelegt:

- Konto: `admin`
- Passwort: `admin123`
- Status: erzwungene Passwortänderung nach dem ersten Login

Der Signaturschlüssel wird in `backend/data/secret.key` gespeichert (wird beim ersten Start automatisch generiert). Bewahre diese Datei und `users.json` in der Produktion sorgfältig auf und ändere das Standardpasswort.

Detaillierte API-Definitionen findest du in den Routendateien unterhalb von `backend/app/routers/`.

## Passwort zurücksetzen

Wenn du das Administratorpasswort vergisst oder dich nicht im Web-Panel anmelden kannst, kannst du es direkt auf dem Server über ein CLI-Skript zurücksetzen (ohne das Backend zu starten):

```bash
cd backend

# Alle Konten auflisten
python reset_password.py --list

# Ein bestimmtes Konto zurücksetzen (neues Passwort interaktiv eingeben)
python reset_password.py admin

# Ohne Konto fragt das Skript zur Auswahl
python reset_password.py
```

Das Skript liest und schreibt direkt `backend/data/users.json`, versteckt die Passworteingabe und entfernt nach dem Zurücksetzen automatisch das Flag „Passwort beim ersten Login ändern". Das neue Passwort muss mindestens 6 Zeichen lang sein.

## Konfiguration

Die Proxy-Konfiguration des Frontend-Dev-Servers liegt unter `frontend/vite.config.js`; standardmäßig werden `/api` und WebSocket an `http://localhost:8000` weitergeleitet:

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

## Mitwirken

Issue und Pull Request sind willkommen.

## License

AGPLv3