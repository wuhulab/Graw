# Graw

Un panel de administración de servidores basado en web con un diseño de interacción tipo sistema operativo de escritorio. El frontend usa Vue 3 + Vite; el backend usa FastAPI. Proporciona monitorización del sistema en tiempo real, gestión de Docker, gestión de procesos y de archivos, terminal web y notas.

## ¿Cómo descargarlo?

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

Cambia `8041` por tu puerto. Graw usa un envoltorio unificado en Docker: basta con un solo puerto, no hace falta configurar varios registros.

## Características

- **Sistema de cuentas y permisos** — acceso basado en JWT, roles (administrador / usuario normal), gestión de cuentas y cambio forzado de contraseña. Tras iniciar sesión, todas las API protegidas requieren `Authorization: Bearer <token>`
- **Interfaz tipo escritorio** — aplicaciones en ventanas, barra de tareas, accesos directos, arrastrar y soltar, maximizar/minimizar
- **Monitorización en tiempo real** — CPU, memoria, disco, red y carga, transmitidos por WebSocket con gráficos
- **Gestión de sitios web** — CRUD de hosts virtuales Nginx / Apache, iniciar/detener, generar y ver la configuración
- **Gestión de bases de datos** — conexiones MySQL / MariaDB / Redis, exploración de BD/tablas, ejecución de comandos SQL / Redis
- **Tareas programadas** — gestión de expresiones Cron (envoltorio sobre Linux crontab / Windows schtasks)
- **Firewall** — reglas de puertos y listas de IP permitidas/bloqueadas (iptables / netsh)
- **Certificados SSL** — subir certificados propios y solicitar Let's Encrypt (certbot)
- **Centro de registros** — ver y vaciar los registros del sistema, de los sitios y del panel
- **Gestión de Docker** — ver contenedores e imágenes, iniciar, detener, ver registros, etc.
- **Gestión de procesos** — ver la lista y los detalles de los procesos en ejecución
- **Gestión de archivos** — explorar directorios, subir/descargar, cambiar permisos, comprimir/extraer, copiar y renombrar
- **Terminal web** — una terminal en el navegador basada en xterm.js para operar el servidor directamente (autenticada por WebSocket mediante `?token=`)
- **Notas** — anotar y consultar notas del sistema

## Pila tecnológica

| Capa | Tecnología |
|------|------|
| Frontend | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| Backend | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| Comunicación | REST API + WebSocket |

## Estructura de directorios

```
Graw/
├── frontend/          # Frontend Vue 3
│   ├── src/
│   │   ├── components/     # componentes de escritorio, ventanas, barra de tareas y tarjetas
│   │   ├── api.js          # envoltorio de la API del backend
│   │   └── App.vue         # componente raíz (escritorio)
│   ├── package.json
│   └── vite.config.js
├── backend/           # Backend FastAPI
│   ├── app/
│   │   ├── main.py         # punto de entrada de la aplicación
│   │   └── routers/        # rutas por módulo (system, docker, process, files, terminal, notes)
│   ├── api/                # rutas compatibles con versiones anteriores (se puede citar directamente)
│   └── requirements.txt
├── start.bat          # inicio con un clic en Windows
├── start.sh           # inicio con un clic en Linux / macOS
└── README.md
```

## Inicio rápido

### Requisitos

- Python 3.8+
- Node.js 16+
- (Opcional) motor Docker, necesario para la gestión de Docker

### Inicio manual

**1. Iniciar el backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # la primera vez
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# O, si estás desarrollando:
py start.py

```

**2. Iniciar el frontend**

```bash
cd frontend
pnpm install
pnpm run dev
```

### Compilación de producción

La compilación de producción del frontend se genera en `frontend/dist`; el backend detecta y monta automáticamente ese directorio como recursos estáticos:

```bash
cd frontend
npm run build
```

Después, simplemente inicia el backend y accede a la aplicación completa en `http://localhost:8000`.

## Resumen de la API

| Módulo | Prefijo | Descripción |
|------|------|------|
| Auth | `/api/auth` | acceso, usuario actual, cambio de contraseña, gestión de usuarios (admin) |
| System | `/api/system` | CPU, memoria, disco, red, carga, flujo WebSocket en tiempo real |
| Sites | `/api/sites` | gestión de hosts virtuales (Nginx/Apache) |
| Databases | `/api/databases` | gestión de conexiones y consultas MySQL/MariaDB/Redis |
| Cron | `/api/cron` | tareas programadas (Linux crontab / Windows schtasks) |
| Firewall | `/api/firewall` | reglas de firewall por puertos e IP |
| SSL | `/api/ssl` | subir certificados propios y solicitar Let's Encrypt |
| Logs | `/api/logs` | ver y vaciar registros |
| Docker | `/api/docker` | gestión de contenedores e imágenes |
| Process | `/api/process` | lista de procesos y detalles |
| Files | `/api/files` | exploración de archivos, transferencia, permisos, compresión/extracción |
| Terminal | `/api/terminal` | sesiones de terminal WebSocket (auth por `?token=`) |
| Notes | `/api/notes` | CRUD de notas |

Salvo `/api/auth/login` y `/api/health`, todos los endpoints requieren la cabecera `Authorization: Bearer <token>`.

## Cuenta por defecto

En el primer arranque se crea automáticamente una entrada en `backend/data/users.json`:

- Cuenta: `admin`
- Contraseña: `admin123`
- Estado: cambio forzado de contraseña tras el primer inicio

La clave de firma se guarda en `backend/data/secret.key` (se genera automáticamente en el primer arranque). En producción, guarda bien este archivo y `users.json`, y cambia la contraseña por defecto.

Para las definiciones detalladas de la API, consulta los archivos de rutas en `backend/app/routers/`.

## Restablecer contraseña

Si olvidas la contraseña del administrador o no puedes entrar en la web, puedes restablecerla directamente en el servidor con un script CLI (no hace falta iniciar el backend):

```bash
cd backend

# Listar todas las cuentas
python reset_password.py --list

# Restablecer una cuenta concreta (introduce la nueva contraseña interactivamente)
python reset_password.py admin

# Sin indicar cuenta, el script te pedirá que elijas
python reset_password.py
```

El script lee/escribe directamente `backend/data/users.json`, oculta la entrada de la contraseña y, tras el restablecimiento, elimina automáticamente la marca «cambiar contraseña en el primer inicio». La nueva contraseña debe tener al menos 6 caracteres.

## Configuración

La configuración de proxy del servidor de desarrollo del frontend está en `frontend/vite.config.js`; por defecto reenvía `/api` y WebSocket a `http://localhost:8000`:

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

## Contribuciones

Se agradecen los Issue y las Pull Request.

## License

AGPLv3