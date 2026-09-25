# Graw

Un panel de administración de servidores basado en web, con un diseño de interacción «tipo sistema operativo de escritorio» (ventanas, barra de tareas, accesos directos) e integrando un modo de panel estándar al estilo 1Panel. El frontend usa Vue 3 + Vite y el backend, FastAPI.

Además del equipo local, Graw puede integrar otras máquinas como «nodos hijo» en un único panel mediante un **túnel Agent + claves de acceso emparejadas**: cambia de máquina en un solo lugar y administra los contenedores, sitios, archivos, terminal y cortafuegos de varios servidores.

### README en varios idiomas

[Chino simplificado](../README.md) ·
[Chino tradicional](./README.zh-TW.md) ·
[Inglés](./README.en.md) ·
[Japonés](./README.ja.md) ·
[Coreano](./README.ko.md) ·
[Ruso](./README.ru.md) ·
[Español](./README.es.md) ·
[Francés](./README.fr.md) ·
[Alemán](./README.de.md) ·
[Portugués](./README.pt.md) ·
[Esperanto](./README.eo.md)


## Enlaces relacionados

| Proyecto | Dirección |
|------|------|
| Repositorio de código | <https://github.com/wuhulab/Graw> |
| Recetas de la tienda de aplicaciones | <https://github.com/wuhulab/Graw-app-store> |
| Imagen Docker | <https://hub.docker.com/r/shunx/graw> |
| Sitio web oficial | <https://graw.shunx.top/> |
| Reporte de problemas | <https://github.com/wuhulab/Graw/issues> |
| Apoyar con una donación | <https://afdian.com/a/shunianssy> |

## ¿Cómo descargarlo?

Graw se ejecuta como contenedor, pero **todas sus operaciones de administración (contenedores/imágenes Docker, instalación desde la tienda de aplicaciones, terminal web, procesos/cortafuegos, configuración de sitios, etc.) deben aplicarse al host**. Por eso **no basta** con levantar el contenedor de forma básica con `-p puerto:8000`: hay que iniciarlo en el «modo host completo» que se describe a continuación, de modo que el contenedor pueda acceder al Docker del host (socket) y a la raíz del host (`/host`), y cuente con privilegios de nivel de host (`privileged` + `pid host`).

**Opción 1: servidor Linux (recomendada; red host para escuchar directamente el puerto 8000 del host)**

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

Tras iniciarlo, visita `http://<IP-del-servidor>:8000`.

**Opción 2: red bridge (puerto de acceso personalizado, por ejemplo 8041)**

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

Tras iniciarlo, visita `http://<IP-del-servidor>:8041`.

**Opción 3: Docker Compose (el repositorio ya incluye la orquestación con privilegios elevados)**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

Significado de cada parámetro (todos necesarios para que el panel administre el host por completo):

- `--privileged`: concede al contenedor todas las capacidades del kernel; sin esto, `chroot /host`, iptables/cortafuegos, montajes, etc. no funcionan dentro del contenedor.
- `--pid host`: comparte el espacio de nombres de procesos del host; solo así la gestión de procesos y la monitorización del sistema ven todos los procesos del host.
- `--network host`: usa la red del host y el panel escucha directamente el puerto 8000 del host (en la opción 2 se usa en su lugar `-p asignación de puertos`).
- `-v /:/host:rslave` + `HOST_ROOT=/host`: monta la raíz del host en `/host` dentro del contenedor, y el panel opera archivos y comandos del host mediante `chroot /host` (nginx/certbot/crontab, etc.).
- `-v /var/run/docker.sock:/var/run/docker.sock`: conecta con el motor Docker del host (gestión de contenedores/imágenes/registros).
- `/opt/graw/data` es el directorio de datos del panel (montado desde el host); `GRAW_HOST_DATA=/opt/graw/data` indica dónde está el archivo docker-compose en el host, requisito para que la tienda de aplicaciones Docker complete la instalación.

> ⚠️ **Advertencia de seguridad**: este contenedor tiene en la práctica capacidad de operar como root en el host, por lo que solo se recomienda desplegarlo en entornos de confianza. **Cambia la contraseña por defecto inmediatamente** tras el primer inicio de sesión y protege bien los archivos de credenciales de `backend/data/`.

## Características

- **Sistema de cuentas y permisos** — inicio de sesión con JWT, roles (administrador/usuario normal), gestión de cuentas, cambio forzado de contraseña, registro de inicios de sesión, gestión de sesiones en línea (en línea según la vigencia del token y la última actividad)
- **Dos modos de interfaz** — modo escritorio (ventanas/barra de tareas/accesos directos, con arrastrar y maximizar/minimizar) y modo de panel estándar al estilo 1Panel (menú lateral agrupado + pestañas)
- **Gestión de múltiples nodos** — el panel principal integra nodos hijo mediante túnel Agent + claves de acceso emparejadas; admite despliegue de claves SSH, cambio de host a nivel de petición y control de capacidades de los nodos hijo remotos
- **Monitorización del sistema en tiempo real** — CPU, memoria, disco, red y carga, con datos y gráficos en tiempo real vía WebSocket y consulta de métricas históricas
- **Gestión de sitios web** — CRUD de hosts virtuales Nginx / OpenResty / Apache, inicio/parada, generación y consulta de la configuración; convivencia con 1Panel/OpenResty y descubrimiento automático de sitios externos
- **WAF y mejoras del sitio** — cortafuegos de aplicaciones web, pseudostática/rewrite, caché y configuración avanzada del sitio, estadísticas del sitio
- **Gestión de bases de datos** — conexiones MySQL / MariaDB / Redis / PostgreSQL / MongoDB, exploración de bases y tablas, ejecución de SQL / comandos Redis, análisis de consultas lentas
- **Gestión de Docker** — ver contenedores e imágenes, iniciar, detener, registros y estadísticas de recursos; compatible con el formato de salida de docker y podman
- **Tienda de aplicaciones** — instalación de aplicaciones con un clic a partir de recetas YAML (instalar = `docker compose`), con edición personalizada del compose y registros de instalación
- **Gestión de archivos** — explorar directorios, subir/descargar, cambiar permisos, comprimir/extraer, copiar y renombrar; portapapeles estilo Windows (Ctrl+C/V/Delete) y carga de carpetas por arrastre
- **Papelera de reciclaje** — los archivos eliminados por error van a la papelera, con restauración y limpieza automática por días (en todos los nodos)
- **Terminal web** — terminal en el navegador basada en xterm.js para operar el servidor directamente (WebSocket autenticado mediante `?token=`)
- **Tareas programadas / Cortafuegos / SSL** — gestión de expresiones Cron (crontab / schtasks), puertos y listas blancas/negras de IP (iptables / netsh, incluido el control de entrada/salida de los puertos publicados por Docker), subida de certificados y solicitud Let's Encrypt
- **Seguridad y operación** — entrada segura ShunX, protección anti-manipulación web (alertas en tiempo real por WS), reglas de cortafuegos unificadas, chequeo de estado, copia de seguridad del panel, monitorización de servicios, detección de caducidad de certificados, centro de notificaciones
- **Otros** — centro de registros, gestión de procesos, notas, penetración de red interna (Frp), almacenamiento en red, usuarios FTP, gestión de versiones de PHP, caja de herramientas, protocolo abierto de complementos (GPOP)

## Pila tecnológica

| Capa | Tecnología |
|------|------|
| Frontend | Vue 3 (Composition API), Vite 5, Axios, ECharts / vue-echarts, xterm.js, vue-i18n |
| Backend | Python 3.11, FastAPI 0.115, Uvicorn, Pydantic 2, psutil, docker SDK |
| Comunicación | REST API (`/api/*`) + WebSocket (flujo de monitorización, terminal) |
| Despliegue | compilación Docker por etapas (compilación del frontend con Node → entorno de ejecución del backend en Python) |

## Estructura de directorios

```
Graw/
├── frontend/                 # Frontend Vue 3
│   ├── src/
│   │   ├── components/       # componentes de escritorio, ventanas, barra de tareas y tarjetas
│   │   │   └── windows/      # una ventana independiente por función (*Window.vue)
│   │   ├── store/            # estados reactive en singleton (auth / systemMetrics / docker ...)
│   │   ├── locales/          # idiomas de vue-i18n (22 en total)
│   │   └── App.vue           # componente raíz (cambio entre escritorio y modo panel)
│   ├── vite.config.js        # proxy de desarrollo /api (incluye ws) → :8000
│   └── package.json
├── backend/                  # Backend FastAPI
│   ├── app/
│   │   ├── main.py           # punto de entrada: registro de rutas, middleware y tareas de fondo del lifespan
│   │   ├── auth.py           # dependencias de autenticación JWT y creación de usuarios
│   │   ├── agent_*.py        # autenticación / configuración / túnel proxy del Agent del nodo hijo
│   │   ├── node_manager.py   # contexto multinodo y cambio de host a nivel de petición
│   │   ├── hostfs.py         # capa de adaptación del sistema de archivos del host (chroot /host)
│   │   ├── routers/          # rutas de cada módulo de negocio
│   │   └── data/             # datos en tiempo de ejecución (gitignore, permisos restringidos)
│   ├── test_*_unit.py        # pruebas unitarias de pytest (test_*_e2e.py son casos de extremo a extremo)
│   └── requirements.txt
├── app-store/                # recetas YAML e iconos de la tienda de aplicaciones comunitaria
├── plugin-examples/          # ejemplos del protocolo abierto de complementos (GPOP)
├── readme-i18n/              # README en varios idiomas
├── docs/                     # documentación adicional (protocolo de complementos, etc.)
├── agent/                    # recursos y habilidades relacionados con el Agent del nodo hijo
├── Dockerfile                # compilación por etapas (frontend → entorno de ejecución del backend)
├── docker-compose.yml        # orquestación de altos privilegios para «administrar el host por completo»
├── start.sh / start.bat      # arranque en un clic para desarrollo local (backend + frontend)
└── AGENTS.md                 # arquitectura y convenciones de desarrollo (lectura obligatoria antes de tocar el código)
```

## Inicio rápido

### Requisitos

- Python 3.8+ (la imagen de producción usa 3.11)
- Node.js 16+
- (Opcional) motor Docker, para la funcionalidad de gestión de Docker

### Arranque en un clic (desarrollo)

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

El script crea el entorno virtual cuando hace falta, instala las dependencias y arranca a la vez el backend y el frontend.

### Arranque manual

**1. Arrancar el backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # la primera vez
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# o usa el script de desarrollo
python start.py
```

**2. Arrancar el frontend**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173, Vite redirige /api y ws a :8000
```

> En PowerShell/cmd de Windows no se admite `&&`; separa varios comandos con punto y coma `;`.

### Compilación de producción

La compilación de producción del frontend se genera en `frontend/dist`; el backend detecta y monta ese directorio automáticamente como recursos estáticos:

```bash
cd frontend
npm run build
```

Después basta con arrancar el backend para acceder a la aplicación completa en `http://localhost:8000` (el frontend lo sirve el backend desde el mismo origen, sin configuración de CORS). También puedes compilar la imagen y desplegarla directamente con el `Dockerfile` / `docker-compose.yml` del repositorio.

## Resumen de la API

Todas las interfaces llevan el prefijo `/api/*`; salvo `/api/auth/login` y `/api/health`, todas requieren la cabecera `Authorization: Bearer <token>`.

| Módulo | Prefijo | Descripción |
|------|------|------|
| Auth | `/api/auth` | Inicio de sesión, usuario actual, cambio de contraseña, gestión de usuarios (administrador) |
| Agent | `/api/agent` | Autenticación entre nodos hijo (intercambio de claves emparejadas por JWT) |
| System | `/api/system` | CPU, memoria, disco, red, carga y flujo WebSocket en tiempo real |
| Nodes | `/api/nodes` | Gestión de múltiples nodos y cambio del host administrado actual |
| Sites | `/api/sites` | Gestión de hosts virtuales (Nginx / OpenResty / Apache) |
| WAF | `/api/waf` | Reglas del cortafuegos de aplicaciones web y protección de sitios |
| Databases | `/api/databases` | Conexiones y consultas MySQL / MariaDB / Redis / PostgreSQL / MongoDB |
| Docker | `/api/docker` | Contenedores, imágenes, volúmenes y edición de contenedores |
| Files | `/api/files` | Exploración, transferencia, permisos, compresión y extracción de archivos |
| Recycle | `/api/recycle` | Papelera de reciclaje (restauración, limpieza automática) |
| Terminal | `/api/terminal` | Sesiones de terminal por WebSocket (autenticación mediante `?token=`) |
| App Store | `/api/appstore` | Instalación y gestión de recetas de la tienda de aplicaciones |
| Cron / Firewall / SSL | `/api/cron`, `/api/firewall`, `/api/ssl` | Tareas programadas / cortafuegos / certificados |
| Plugins | `/api/plugins`, `/api/op` | Gestión de complementos y API abierta de complementos (GPOP) |

La lista completa de rutas y los niveles de autenticación (`PROTECTED` / `ADMIN` / autenticación interna del endpoint) están en la sección 3 de [AGENTS.md](../AGENTS.md). La documentación de la API está desactivada por defecto; para depurar, define la variable de entorno `GRAW_ENABLE_DOCS=1` y accede a `/docs`.

## Cuenta por defecto

En el primer arranque se crea automáticamente `backend/data/users.json`:

- Usuario: `admin`
- Contraseña: `admin123`
- Estado: cambio de contraseña forzado tras el primer inicio de sesión

La clave de firma se guarda en `backend/data/secret.key` (se genera automáticamente en el primer arranque). En producción, guarda bien este archivo y `users.json`, y cambia la contraseña por defecto.

## Restablecer la contraseña

Si olvidaste la contraseña de administrador o no puedes acceder al panel web, puedes restablecerla directamente en el servidor con un script de línea de comandos (no hace falta arrancar el servicio backend):

```bash
cd backend

# listar todas las cuentas
python reset_password.py --list

# restablecer una cuenta concreta (introduce la nueva contraseña de forma interactiva)
python reset_password.py admin

# sin indicar cuenta, el script te pedirá que la elijas
python reset_password.py
```

El script lee y escribe directamente `backend/data/users.json`, oculta la entrada de la contraseña y, tras el restablecimiento, limpia automáticamente el indicador «cambiar la contraseña en el primer inicio de sesión». La nueva contraseña debe tener al menos 6 caracteres.

## Configuración

La configuración del proxy del servidor de desarrollo del frontend está en `frontend/vite.config.js`; por defecto redirige `/api` y WebSocket a `http://localhost:8000`:

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

Variables de entorno más habituales:

| Variable | Descripción |
|------|------|
| `HOST_ROOT` | punto de montaje de la raíz del host dentro del contenedor (p. ej. `/host`); activa el modo host |
| `GRAW_HOST_DATA` | ruta real del directorio `data` del panel en el host; necesaria para la instalación desde la tienda de aplicaciones |
| `GRAW_ENABLE_DOCS` | si se define como `1`, abre `/docs`, `/redoc` y `/openapi.json` |
| `GRAW_SESSION_ONLINE_SECONDS` | umbral de inactividad (en segundos) para considerar una sesión en línea; por defecto, 2 horas |
| `TZ` | zona horaria del contenedor, por ejemplo `Asia/Shanghai` |

## Documentación del proyecto

- [AGENTS.md](../AGENTS.md) — arquitectura, convenciones y errores frecuentes (**léelo antes de modificar código**)
- [CONTRIBUTING.md](../CONTRIBUTING.md) — guía de contribución y acuerdo de licencia para contribuyentes (CLA)
- [SECURITY.md](../SECURITY.md) — procedimiento para reportar problemas de seguridad
- [CHANGELOG.md](../CHANGELOG.md) — historial de cambios de versiones
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) — protocolo abierto de complementos (GPOP)
- [app-store/](../app-store/) — recetas de la tienda de aplicaciones (YAML)
- [plugin-examples/](../plugin-examples/) — ejemplos de complementos


## Contribuir

Las Issues y los Pull Requests son bienvenidos; consulta [CONTRIBUTING.md](../CONTRIBUTING.md) para más detalles.

> **Acuerdo de licencia para contribuyentes (CLA)**: al enviar código, documentación u otro contenido a este proyecto, **aceptas automáticamente** el [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla), lo que incluye autorizar a **WuHuLaB** a conservar el derecho de usar tu contribución con **fines comerciales** y de relicenciarla/distribuirla bajo una **licencia cerrada (propietaria)**.

## Donaciones

Si Graw te resulta útil, ¡invita al autor a un café ☕!

- Afdian: <https://afdian.com/a/shunianssy>
- Rainyun (patrocinador, servidores económicos): <https://www.rainyun.com/NjQwNjg5_>

## License

Este proyecto se publica como código abierto bajo [AGPLv3](../LICENSE).

De acuerdo con el [acuerdo de licencia para contribuyentes (CLA)](../CONTRIBUTING.md#7-贡献者许可协议cla), WuHuLaB se reserva el derecho de usar este proyecto (incluidas las contribuciones de la comunidad) con **fines comerciales** y de relicenciarlo o distribuirlo bajo una **licencia cerrada (propietaria)**.