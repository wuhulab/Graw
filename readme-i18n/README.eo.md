# Graw

Ret-bazita servila administra panelo, kiu uzas interagodesegnon «similan al labortabla operaciumo» (fenestroj, tasko-strio, labortablaj ŝparvojoj) kaj ankaŭ integrigas norman panelan reĝimon laŭ stilo 1Panel. La fronto uzas Vue 3 + Vite, la malantaŭo uzas FastAPI.

Krom la loka maŝino, Graw povas ankaŭ alpreni aliajn gastigojn en unuecan panelon kiel «infano-nodoj» per **Agent-tunelo + parigitaj alirŝlosiloj**: ŝanĝu gastigon en unu loko kaj administru la ujojn, retejojn, dosierojn, terminalojn kaj fajroŝirmilojn de pluraj serviloj.

### Plurlingva README

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


## Rilataj ligiloj

| Projekto | Adreso |
|------|------|
| Fontkoda deponejo | <https://github.com/wuhulab/Graw> |
| Receptoj de la aplikaĵ-butiko | <https://github.com/wuhulab/Graw-app-store> |
| Docker-bildo | <https://hub.docker.com/r/shunx/graw> |
| Oficiala retejo | <https://graw.shunx.top/> |
| Raporti problemon | <https://github.com/wuhulab/Graw/issues> |
| Subteni per donaco | <https://afdian.com/a/shunianssy> |

## Kiel instali ĝin?

Graw funkcias kiel ujo, sed **ĉiuj ĝiaj administraj operacioj (Docker-ujoj/bildoj, instalado el la aplikaĵ-butiko, reta terminalo, procezoj/fajroŝirmilo, reteja agordo ktp.) devas agi sur la gastiga maŝino**. Tial **ne** eblas simple lanĉi nudan ujon per io kiel `-p haveno:8000`; necesas lanĉi ĝin laŭ la sube priskribita «plena gastiga reĝimo»: la ujo devas povi aliri la Docker de la gastigo (ŝtopilo), la radikan dosierujon de la gastigo (`/host`) kaj posedi rajtojn je gastiga nivelo (`privileged` + `pid host`).

**Opcio 1: Linux-servilo (rekomendata, uzas la gastigan reton por rekte aŭskulti la gastigan havenon 8000)**

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

Post lanĉo, aliru `http://<servila IP>:8000`.

**Opcio 2: Bridge-reto (propra alirhaveno, ekzemple 8041)**

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

Post lanĉo, aliru `http://<servila IP>:8041`.

**Opcio 3: Docker Compose (altrajta orkestrado jam provizita en la deponejo)**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

Signifo de ĉiu parametro (necesaj por ke la panelo plene administru la gastigon):

- `--privileged`: donas al la ujo ĉiujn kernajn kapablojn; alie operacioj kiel `chroot /host`, iptables/fajroŝirmilo kaj muntadoj ne povas efiki en la ujo.
- `--pid host`: kunhavigas la procezan nomspacon de la gastigo, por ke proceza administrado/sistema monitorado vidu ĉiujn gastigajn procezojn.
- `--network host`: uzas la gastigan reton, por ke la panelo rekte aŭskultu la gastigan havenon 8000 (Opcio 2 anstataŭe uzas `-p` havenan mapadon).
- `-v /:/host:rslave` + `HOST_ROOT=/host`: muntas la radikan dosierujon de la gastigo en la ujon ĉe `/host`; la panelo tiel funkciigas gastigajn dosierojn kaj komandojn (nginx/certbot/crontab ktp.) per `chroot /host`.
- `-v /var/run/docker.sock:/var/run/docker.sock`: konektiĝas al la Docker-motoro de la gastigo (administrado de ujoj/bildoj/protokoloj).
- `/opt/graw/data` estas la datumdosierujo de la panelo (ligita al la gastigo); `GRAW_HOST_DATA=/opt/graw/data` informas la gastigon kie troviĝas la dosiero docker-compose, kio necesas por ke la Docker-aplikaĵ-butiko povu fini instaladon.

> ⚠️ **Sekureca averto**: la supra ujo fakte posedas operacian kapablon je radika nivelo de la gastigo, do oni rekomendas disfaldi ĝin nur en fidinda medio. Bonvolu **tuj ŝanĝi la defaŭltan pasvorton** post la unua ensaluto kaj zorge protekti la identigilajn dosierojn sub `backend/data/`.

## Funkcioj

- **Konta kaj rajtiga sistemo** — uzanta ensaluto bazita sur JWT, roloj (administranto / ordinara uzanto), konta administrado, deviga pasvortoŝanĝo, ensaluta protokolo, administrado de retaj sesioj (reta stato determiniĝas laŭ tokena validperiodo kaj lasta aktiveco)
- **Du interfacaj formoj** — labortabla reĝimo (fenestroj / tasko-strio / labortablaj ŝparvojoj, kun trenado, maksimumigo/minimumigo) kaj norma panela reĝimo laŭ stilo 1Panel (grupigita flanka menuo + pluraj langetoj)
- **Plurnoda administrado** — la ĉefa panelo alprenas infano-nodojn per Agent-tunelo + parigitaj alirŝlosiloj, kun disfaldado de SSH-ŝlosiloj, gastiga ŝanĝo je peta nivelo kaj kapabla limigo de remotaj infano-nodoj
- **Realtempa sistema monitorado** — CPU, memoro, disko, reto kaj ŝarĝo, realtempe sendataj per WebSocket kun datumoj kaj grafikaĵoj, kaj demandoj de historiaj metrikoj
- **Retej-administrado** — kreado/legado/ĝisdatigo/forigo de virtualaj gastigaj retejoj Nginx / OpenResty / Apache, lanĉo/ĉesigo, generado kaj vidado de agordo; povas kunekzisti kun 1Panel/OpenResty kaj aŭtomate malkovri eksterajn retejojn
- **WAF kaj retej-plibonigoj** — Retaplikada Fajroŝirmilo (WAF), rewrite-reguloj, kaŝmemora kaj retej-pliboniga agordo, retejaj statistikoj
- **Datumbaza administrado** — administrado de konektoj MySQL / MariaDB / Redis / PostgreSQL / MongoDB, trarigardo de datumbazoj/tabeloj, ekzekuto de komandoj SQL / Redis, analizo de malrapidaj petoj
- **Docker-administrado** — vidado, lanĉo, ĉesigo, protokoloj kaj rimedaj statistikoj de ujoj kaj bildoj; kongrua kun la eligoformatoj de docker kaj podman
- **Aplikaĵ-butiko** — unuklaka instalado de aplikaĵoj el YAML-receptoj (instalado estas `docker compose`), kun propra compose-redaktado kaj instalaj protokoloj
- **Dosiera administrado** — trarigardo de dosierujoj, alŝuto/elŝuto, ŝanĝo de rajtoj, kunpremo/malpremo, kopio/alinomigo; poŝo laŭ Windows-stilo (Ctrl+C/V/Delete) kaj alŝuto de dosierujoj per treno kaj demeto
- **Rubujo** — erare forigitaj dosieroj iras al la rubujo, kun restaŭro kaj aŭtomata ĉiutaga purigado (trans nodoj)
- **Reta terminalo** — terminalo en la retumilo bazita sur xterm.js por rekte funkciigi la servilon (WebSocket aŭtentigita per `?token=`)
- **Planitaj taskoj / Fajroŝirmilo / SSL** — administrado de Cron-esprimoj (crontab / schtasks), blankaj/nigraj listoj de havenoj kaj IP (iptables / netsh, inkluzive de enira/elira kontrolo de publikigitaj Docker-havenoj), alŝuto de atestoj kaj peto de Let's Encrypt
- **Sekureco kaj operacioj** — sekura enirejo ShunX, protekto kontraŭ reteja falsado (realtempa WS-alarmo), unuigitaj fajroŝirmilaj reguloj, san-kontrolo, panela sekurkopio, serva monitorado, detekto de atestekspiro, sciiga centro
- **Aliaj** — protokola centro, proceza administrado, notoj, intranet-penetro (Frp), reta konservado, FTP-uzantoj, PHP-versia administrado, ilujo, malfermita kromaĵa protokolo (GPOP)

## Teknologia stako

| Tavolo | Teknologio |
|------|------|
| Fronto | Vue 3 (Composition API), Vite 5, Axios, ECharts / vue-echarts, xterm.js, vue-i18n |
| Malantaŭo | Python 3.11, FastAPI 0.115, Uvicorn, Pydantic 2, psutil, docker SDK |
| Komunikado | REST API (`/api/*`) + WebSocket (monitorada fluo, terminalo) |
| Disfaldado | Pluretapa Docker-konstruo (fronta Node-konstruo → malantaŭa Python-rultempo) |

## Kataloga strukturo

```
Graw/
├── frontend/                 # Vue 3 fronto
│   ├── src/
│   │   ├── components/       # komponantoj de labortablo, fenestroj, tasko-strio, kartoj
│   │   │   └── windows/      # unu sendependa fenestra komponanto por ĉiu funkcio (*Window.vue)
│   │   ├── store/            # reaktivaj unuoblaj statoj (auth / systemMetrics / docker ...)
│   │   ├── locales/          # vue-i18n plurlingvaj (entute 22)
│   │   └── App.vue           # radika komponanto (labortabla medio / panela reĝima ŝanĝo)
│   ├── vite.config.js        # evoluiga prokurilo /api (inkl. ws) → :8000
│   └── package.json
├── backend/                  # FastAPI malantaŭo
│   ├── app/
│   │   ├── main.py           # aplikaĵa enirpunkto: voja registrado, mezaĵoj, lifespan-fonaj taskoj
│   │   ├── auth.py           # JWT-aŭtentigaj dependoj kaj uzanta semado
│   │   ├── agent_*.py        # aŭtentigo / agordo / tunela prokurilo de infano-nodaj Agentoj
│   │   ├── node_manager.py   # plurnoda kunteksto kaj gastiga ŝanĝo je peta nivelo
│   │   ├── hostfs.py         # adapta tavolo de la gastiga dosiersistemo (chroot /host)
│   │   ├── routers/          # komercaj vojoj laŭ modulo
│   │   └── data/             # rultempaj datumoj (gitignore, striktigitaj rajtoj)
│   ├── test_*_unit.py        # pytest-unuotestoj (test_*_e2e.py estas fin-al-fina kazoj)
│   └── requirements.txt
├── app-store/                # YAML-receptoj kaj ikonoj de la komunuma aplikaĵ-butiko
├── plugin-examples/          # ekzemploj de la malfermita kromaĵa protokolo (GPOP)
├── readme-i18n/              # plurlingvaj README
├── docs/                     # suplementaj dokumentoj (kromaĵa protokolo ktp.)
├── agent/                    # rimedoj kaj kapabloj rilataj al infano-nodaj Agentoj
├── Dockerfile                # plur-etapa konstruo (fronta konstruo → malantaŭa rultempo)
├── docker-compose.yml        # altrajta orkestrado «plena gastiga administrado»
├── start.sh / start.bat      # unuklaka lanĉo por loka disvolvado (malantaŭo + fronto)
└── AGENTS.md                 # arkitekturo de la kodobazo kaj disvolvaj konvencioj (legu antaŭ ŝanĝi kodon)
```

## Rapida komenco

### Postuloj

- Python 3.8+ (produkta bildo estas 3.11)
- Node.js 16+
- (Opcia) Docker-motoro, por la Docker-administrado

### Unuklaka lanĉo (disvolvado)

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

La skripto laŭbezone kreas virtualan medion, instalas dependojn kaj samtempe lanĉas la malantaŭon kaj la fronton.

### Mana lanĉo

**1. Lanĉi la malantaŭon**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # unuafoje
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# aŭ uzu la disvolvan lanĉan skripton
python start.py
```

**2. Lanĉi la fronton**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173, Vite prokuras /api kaj ws al :8000
```

> En Windows, PowerShell/cmd ne subtenas `&&`; apartigu plurajn komandojn per punktokomo `;`.

### Produkta konstruo

La produkta konstruo de la fronto estas eligita al `frontend/dist`; la malantaŭo aŭtomate detektas kaj muntas tiun dosierujon kiel statikajn rimedojn:

```bash
cd frontend
npm run build
```

Poste simple lanĉu la malantaŭon kaj aliru la plenan aplikaĵon ĉe `http://localhost:8000` (la fronto estas servata samorigine de la malantaŭo, sen CORS-agordo). Vi ankaŭ povas rekte konstrui kaj disfaldi la bildon per la `Dockerfile` / `docker-compose.yml` en la deponejo.

## API-superrigardo

Ĉiuj finpunktoj havas la prefikson `/api/*`; krom `/api/auth/login` kaj `/api/health`, ĉiuj postulas la ĉapon `Authorization: Bearer <token>`.

| Modulo | Prefikso | Priskribo |
|------|------|------|
| Auth | `/api/auth` | ensaluto, nuna uzanto, pasvortoŝanĝo, uzanta administrado (administranto) |
| Agent | `/api/agent` | maŝin-al-maŝina aŭtentigo de infano-nodoj (parigita ŝlosilo ŝanĝata al JWT) |
| System | `/api/system` | CPU, memoro, disko, reto, ŝarĝo, realtempa WebSocket-fluo |
| Nodes | `/api/nodes` | plurnoda administrado kaj ŝanĝo de la nuna administrata gastigo |
| Sites | `/api/sites` | administrado de virtualaj gastigaj retejoj (Nginx / OpenResty / Apache) |
| WAF | `/api/waf` | reguloj de la Retaplikada Fajroŝirmilo kaj reteja protekto |
| Databases | `/api/databases` | konektoj kaj demandoj MySQL / MariaDB / Redis / PostgreSQL / MongoDB |
| Docker | `/api/docker` | ujoj, bildoj, volumoj kaj uja redaktado |
| Files | `/api/files` | dosiera trarigardo, transporto, rajtoj, kunpremo/malpremo |
| Recycle | `/api/recycle` | rubujo (restaŭro, aŭtomata purigado) |
| Terminal | `/api/terminal` | sesioj de WebSocket-terminalo (aŭtentigo per `?token=`) |
| App Store | `/api/appstore` | instalado kaj administrado de aplikaĵ-butikaj receptoj |
| Cron / Firewall / SSL | `/api/cron`, `/api/firewall`, `/api/ssl` | planitaj taskoj / fajroŝirmilo / atestoj |
| Plugins | `/api/plugins`, `/api/op` | kromaĵa administrado kaj malfermita kromaĵa interfaco (GPOP) |

Por la kompleta vojlisto kaj la aŭtentigaj niveloj (`PROTECTED` / `ADMIN` / mem-aŭtentigo je finpunkta nivelo), vidu sekcion 3 de [AGENTS.md](../AGENTS.md). La API-dokumentaro estas malŝaltita defaŭlte; por sencimigado agordu la medi-variablon `GRAW_ENABLE_DOCS=1` kaj poste aliru `/docs`.

## Defaŭlta konto

Ĉe la unua lanĉo aŭtomate kreiĝas registraĵo en `backend/data/users.json`:

- Konto: `admin`
- Pasvorto: `admin123`
- Statuso: deviga pasvortoŝanĝo post la unua ensaluto

La subskriba ŝlosilo estas konservita en `backend/data/secret.key` (aŭtomate generita ĉe la unua lanĉo). En produktado zorge konservu tiun dosieron kaj `users.json`, kaj ŝanĝu la defaŭltan pasvorton.

## Restarigi la pasvorton

Se vi forgesas la administrantan pasvorton aŭ ne povas ensaluti en la retan panelon, vi povas restarigi ĝin rekte en la servilo per komandlinia skripto (sen lanĉi la malantaŭan servon):

```bash
cd backend

# Listigi ĉiujn kontojn
python reset_password.py --list

# Restarigi specifan konton (interage enigi novan pasvorton)
python reset_password.py admin

# Sen specifi konton, la skripto petos vin elekti
python reset_password.py
```

La skripto rekte legas/skribas `backend/data/users.json`, kaŝas la pasvortan enigon kaj post restarigo aŭtomate forigas la markon «ŝanĝi pasvorton ĉe la unua ensaluto». La nova pasvorto devas havi almenaŭ 6 signojn.

## Agordo

La prokurila agordo de la fronta evoluiga servilo troviĝas en `frontend/vite.config.js`; defaŭlte ĝi plusendas `/api` kaj WebSocket al `http://localhost:8000`:

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

Ofte uzataj medi-variabloj:

| Variablo | Priskribo |
|------|------|
| `HOST_ROOT` | muntopunkto de la gastiga radika dosierujo ene de la ujo (ekz. `/host`), ŝaltas la gastigan reĝimon |
| `GRAW_HOST_DATA` | reala vojo de la panel-dosierujo `data` ĉe la gastigo, necesa por instalado el la aplikaĵ-butiko |
| `GRAW_ENABLE_DOCS` | se agordita al `1`, malfermas `/docs`, `/redoc`, `/openapi.json` |
| `GRAW_SESSION_ONLINE_SECONDS` | senagada sojlo (sekundoj) por determini ĉu sesio estas reta; defaŭlte 2 horoj |
| `TZ` | horzono de la ujo, ekz. `Asia/Shanghai` |

## Projekta dokumentaro

- [AGENTS.md](../AGENTS.md) — arkitekturo, konvencioj kaj oftaj kaptiloj (**bonvolu legi komplete antaŭ ŝanĝi kodon**)
- [CONTRIBUTING.md](../CONTRIBUTING.md) — kontribua gvidilo kaj Kontribuanta Permesila Interkonsento (CLA)
- [SECURITY.md](../SECURITY.md) — proceduro por raporti sekurecajn problemojn
- [CHANGELOG.md](../CHANGELOG.md) — ŝanĝoprotokolo de versioj
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) — malfermita kromaĵa protokolo (GPOP)
- [app-store/](../app-store/) — receptoj de la aplikaĵ-butiko (YAML)
- [plugin-examples/](../plugin-examples/) — kromaĵaj ekzemploj


## Kontribuado

Issue-j kaj Pull Request-j estas bonvenaj; vidu [CONTRIBUTING.md](../CONTRIBUTING.md) por detaloj.

> **Kontribuanta Permesila Interkonsento (CLA)**: per la sendado de kodo, dokumentaro aŭ alia enhavo al ĉi tiu projekto, vi **defaŭlte konsentas** al la [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla), inkluzive de la rajtigo de **WuHuLaB** konservi la rajton uzi vian kontribuon por **komercaj celoj** kaj repermisigi/disdoni ĝin sub **fermitfonta (proprieta) permesilo**.

## Donaci

Se Graw helpis vin, vi estas bonvena pagi tason da kafo al la aŭtoro ☕

- Afdian (爱发电): <https://afdian.com/a/shunianssy>
- RainYun (雨云, sponsoranto, malmultekostaj serviloj): <https://www.rainyun.com/NjQwNjg5_>

## License

Ĉi tiu projekto estas publikigita kiel malfermita fonto sub [AGPLv3](../LICENSE).

Laŭ la [Kontribuanta Permesila Interkonsento (CLA)](../CONTRIBUTING.md#7-贡献者许可协议cla), WuHuLaB retenas la rajton uzi ĉi tiun projekton (inkluzive de komunumaj kontribuoj) por **komercaj celoj** kaj repermisigi aŭ disdoni ĝin sub **fermitfonta (proprieta) permesilo**.