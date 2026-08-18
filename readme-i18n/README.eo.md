# Graw

Ret-bazita servila administra panelo kun interagodesegno simila al komputila operaciumo. La fronto uzas Vue 3 + Vite; la malantaŭo uzas FastAPI. Ĝi provizas realtempan sisteman monitoradon, Docker-administradon, procezon kaj dosieran administradon, reta terminalon kaj notojn.

## Kiel elŝuti?

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

Ŝanĝu `8041` al via haveno. Graw uzas unuecan ĉirkaŭaĵon en Docker: sufiĉas unu haveno, neniu neceso agordi plurajn registraĵojn.

## Funkcioj

- **Sistemo de kontoj kaj rajtoj** — ensaluto bazita sur JWT, roloj (administranto / ordinara uzanto), administrado de kontoj, deviga ŝanĝo de pasvorto. Post ensaluto ĉiuj protektataj API postulas `Authorization: Bearer <token>`
- **Interfaco stila labortablo** — fenestraj aplikaĵoj, tasko-strio, labortablaj ŝparvojoj, trenado kaj demetado, maksimumigo/minimumigo
- **Realtempa sistemo-monitorado** — CPU, memoro, disko, reto, ŝarĝo, realtempe sendata per WebSocket kun grafikaĵoj
- **Retej-administrado** — CRUD de virtualaj gastigaj retejoj Nginx / Apache, lanĉi/ĉesigi, generii kaj vidi agordon
- **Datumbaza administrado** — konektoj MySQL / MariaDB / Redis, trudado de DB/tabeloj, ekzekuto de komandoj SQL / Redis
- **Planitaj taskoj** — administrado de Cron-esprimoj (ĉirkaŭaĵo de Linux crontab / Windows schtasks)
- **Fajroŝirmilo** — reguloj de havenoj kaj permesitaj/malpermesitaj listoj de IP (iptables / netsh)
- **SSL-atestoj** — alŝuto de propraj atestoj kaj peto de Let's Encrypt (certbot)
- **Registra centro** — realtempa vido kaj malplenigo de sistemaj, retejaj kaj panelaj protokoloj
- **Docker-administrado** — vidi ujojn kaj bildojn, lanĉi, ĉesigi, vidi protokolojn ktp.
- **Proceza administrado** — vidi liston kaj detalojn de kurantaj procezoj
- **Dosiera administrado** — trudi repertuarojn, alŝuti/elŝuti, ŝanĝi rajtojn, kunpremi/malpremi, kopii kaj alinomi
- **Reta terminalo** — terminalo en la retumilo bazita sur xterm.js por rekte uzi la servilon (WebSocket aŭtentigita per `?token=`)
- **Notoj** — registri kaj vidi sistemajn notojn

## Teknologia stako

| Tavolo | Teknologio |
|------|------|
| Fronto | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| Malantaŭo | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| Komunikado | REST API + WebSocket |

## Kataloga strukturo

```
Graw/
├── frontend/          # Vue 3 fronto
│   ├── src/
│   │   ├── components/     # komponantoj de labortablo, fenestroj, tasko-strio, karto
│   │   ├── api.js          # ĉirkaŭaĵo de la malantaŭa API
│   │   └── App.vue         # radika komponanto (labortablo)
│   ├── package.json
│   └── vite.config.js
├── backend/           # FastAPI malantaŭo
│   ├── app/
│   │   ├── main.py         # enirpunkto de la aplikaĵo
│   │   └── routers/        # vojoj laŭ modulo (system, docker, process, files, terminal, notes)
│   ├── api/                # kongruaj kun malnovaj versioj (rekte referencaj)
│   └── requirements.txt
├── start.bat          # lanĉo per unu klako en Windows
├── start.sh           # lanĉo per unu klako en Linux / macOS
└── README.md
```

## Rapida komenco

### Postuloj

- Python 3.8+
- Node.js 16+
- (Opcioj) Docker-motoro, necesas por la Docker-administrado

### Mana lanĉo

**1. Lanĉi la malantaŭon**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # unuafoje
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Aŭ, se vi disvolvas:
py start.py

```

**2. Lanĉi la fronton**

```bash
cd frontend
pnpm install
pnpm run dev
```

### Produkta konstruo

La produkta konstruo de la fronto estas kreata en `frontend/dist`; la malantaŭo aŭtomate detektas kaj muntas tiun katalogon kiel statikajn rimedojn:

```bash
cd frontend
npm run build
```

Poste simple lanĉu la malantaŭon kaj aliru la plenan aplikaĵon ĉe `http://localhost:8000`.

## API superrigardo

| Modulo | Prefikso | Priskribo |
|------|------|------|
| Auth | `/api/auth` | ensaluto, nuna uzanto, ŝanĝo de pasvorto, administrado de uzantoj (admin) |
| System | `/api/system` | CPU, memoro, disko, reto, ŝarĝo, realtempa WebSocket-fluo |
| Sites | `/api/sites` | administrado de virtualaj gastigaj retejoj (Nginx/Apache) |
| Databases | `/api/databases` | administrado de konektoj kaj petoj MySQL/MariaDB/Redis |
| Cron | `/api/cron` | planitaj taskoj (Linux crontab / Windows schtasks) |
| Firewall | `/api/firewall` | reguloj de fajroŝirmilo por havenoj kaj IP |
| SSL | `/api/ssl` | alŝuto de propraj atestoj kaj peto Let's Encrypt |
| Logs | `/api/logs` | vido kaj malplenigo de protokoloj |
| Docker | `/api/docker` | administrado de ujoj kaj bildoj |
| Process | `/api/process` | listo de procezoj kaj detaloj |
| Files | `/api/files` | dosiera malserĉado, transporto, rajtoj, kunpremo/malpremo |
| Terminal | `/api/terminal` | sesioj de WebSocket-terminalo (aŭtentigo per `?token=`) |
| Notes | `/api/notes` | CRUD de notoj |

Krom `/api/auth/login` kaj `/api/health`, ĉiuj finpunktoj postulas la ĉapon `Authorization: Bearer <token>`.

## Defaŭlta konto

Ĉe la unua lanĉo aŭtomate kreiĝas registraĵo en `backend/data/users.json`:

- Konto: `admin`
- Pasvorto: `admin123`
- Statuso: deviga ŝanĝo de pasvorto post la unua ensaluto

La subskriba ŝlosilo estas konservita en `backend/data/secret.key` (aŭtomate generita ĉe la unua lanĉo). En produktado zorge konservu tiun dosieron kaj `users.json`, kaj ŝanĝu la defaŭltan pasvorton.

Por detalaj API-difinoj, vidu la vojo-dosierojn sub `backend/app/routers/`.

## Reaktivigi la pasvorton

Se vi forgesas la administran pasvorton aŭ ne povas ensaluti en la retan panelon, vi povas reaktivigi ĝin rekte en la servilo per komandlinia skripto (sen lanĉi la malantaŭan servon):

```bash
cd backend

# Listigi ĉiujn kontojn
python reset_password.py --list

# Reaktivigi specifan konton (enigi novan pasvorton interage)
python reset_password.py admin

# Sen konto, la skripto petos elekti
python reset_password.py
```

La skripto rekte legas/skribas `backend/data/users.json`, kaŝas la pasvortan enigon kaj post reaktivigo aŭtomate forigas la markon «ŝanĝi pasvorton ĉe la unua ensaluto». La nova pasvorto devas havi almenaŭ 6 signojn.

## Agordo

La agordo de la prokurilo de la fronta evoluiga servilo troviĝas en `frontend/vite.config.js`; defaŭlte ĝi plusendas `/api` kaj WebSocket al `http://localhost:8000`:

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

## Kontribuo

Ajna Issue kaj Pull Request estas bonvenaj.

## License

AGPLv3