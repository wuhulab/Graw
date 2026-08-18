# Graw

Um painel de administração de servidores baseado na Web, com um design de interação parecido com um sistema operacional. O frontend usa Vue 3 + Vite; o backend usa FastAPI. Oferece monitoramento do sistema em tempo real, gerenciamento de Docker, processos e arquivos, terminal web e notas.

## Como baixar?

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

Altere `8041` para sua porta. O Graw usa um encapsulamento unificado no Docker: basta uma única porta, não é preciso configurar vários registros.

## Recursos

- **Sistema de contas e permissões** — login baseado em JWT, papéis (administrador / usuário comum), gerenciamento de contas e troca forçada de senha. Após o login, todas as APIs protegidas exigem `Authorization: Bearer <token>`
- **Interface estilo desktop** — aplicativos em janelas, barra de tarefas, atalhos, arrastar e soltar, maximizar/minimizar
- **Monitoramento em tempo real** — CPU, memória, disco, rede e carga, enviados em tempo real via WebSocket com gráficos
- **Gerenciamento de sites** — CRUD de hosts virtuais Nginx / Apache, iniciar/parar, gerar e consultar configuração
- **Gerenciamento de bancos de dados** — conexões MySQL / MariaDB / Redis, navegação por BD/tabelas, execução de comandos SQL / Redis
- **Tarefas agendadas** — gerenciamento de expressões Cron (encapsulamento de Linux crontab / Windows schtasks)
- **Firewall** — regras de portas e listas de IP permitidos/bloqueados (iptables / netsh)
- **Certificados SSL** — envio de certificados próprios e solicitação de Let's Encrypt (certbot)
- **Central de logs** — visualização e limpeza dos registros do sistema, de sites e do painel
- **Gerenciamento de Docker** — conferir contêineres e imagens, iniciar, parar, ver logs etc.
- **Gerenciamento de processos** — listar e consultar processos em execução
- **Gerenciamento de arquivos** — navegar em diretórios, enviar/baixar, alterar permissões, compactar/extrair, copiar e renomear
- **Terminal web** — um terminal no navegador baseado em xterm.js para operar o servidor diretamente (WebSocket autenticado via `?token=`)
- **Notas** — anotar e consultar notas do sistema

## Pilha tecnológica

| Camada | Tecnologia |
|------|------|
| Frontend | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| Backend | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| Comunicação | REST API + WebSocket |

## Estrutura de diretórios

```
Graw/
├── frontend/          # Frontend Vue 3
│   ├── src/
│   │   ├── components/     # componentes de desktop, janelas, barra de tarefas e cartões
│   │   ├── api.js          # encapsulamento da API do backend
│   │   └── App.vue         # componente raiz (desktop)
│   ├── package.json
│   └── vite.config.js
├── backend/           # Backend FastAPI
│   ├── app/
│   │   ├── main.py         # ponto de entrada do aplicativo
│   │   └── routers/        # rotas por módulo (system, docker, process, files, terminal, notes)
│   ├── api/                # rotas compatíveis com versões antigas (podem ser referenciadas diretamente)
│   └── requirements.txt
├── start.bat          # início com um clique no Windows
├── start.sh           # início com um clique no Linux / macOS
└── README.md
```

## Início rápido

### Requisitos

- Python 3.8+
- Node.js 16+
- (Opcional) mecanismo Docker, necessário para o gerenciamento de Docker

### Início manual

**1. Iniciar o backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # na primeira vez
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Ou, se estiver desenvolvendo:
py start.py

```

**2. Iniciar o frontend**

```bash
cd frontend
pnpm install
pnpm run dev
```

### Build de produção

O build de produção do frontend é gerado em `frontend/dist`; o backend detecta e monta automaticamente esse diretório como recursos estáticos:

```bash
cd frontend
npm run build
```

Depois, basta iniciar o backend e acessar o aplicativo completo em `http://localhost:8000`.

## Visão geral da API

| Módulo | Prefixo | Descrição |
|------|------|------|
| Auth | `/api/auth` | login, usuário atual, troca de senha, gerenciamento de usuários (admin) |
| System | `/api/system` | CPU, memória, disco, rede, carga, fluxo WebSocket em tempo real |
| Sites | `/api/sites` | gerenciamento de hosts virtuais (Nginx/Apache) |
| Databases | `/api/databases` | gerenciamento de conexões e consultas MySQL/MariaDB/Redis |
| Cron | `/api/cron` | tarefas agendadas (Linux crontab / Windows schtasks) |
| Firewall | `/api/firewall` | regras de firewall por portas e IP |
| SSL | `/api/ssl` | envio de certificados e solicitação de Let's Encrypt |
| Logs | `/api/logs` | consulta e limpeza de registros |
| Docker | `/api/docker` | gerenciamento de contêineres e imagens |
| Process | `/api/process` | lista de processos e detalhes |
| Files | `/api/files` | navegação, transferência, permissões, compactação/extração |
| Terminal | `/api/terminal` | sessões de terminal WebSocket (auth via `?token=`) |
| Notes | `/api/notes` | CRUD de notas |

Exceto `/api/auth/login` e `/api/health`, todos os endpoints exigem o cabeçalho `Authorization: Bearer <token>`.

## Conta padrão

No primeiro início, é criado automaticamente um registro em `backend/data/users.json`:

- Conta: `admin`
- Senha: `admin123`
- Status: troca forçada de senha após o primeiro login

A chave de assinatura é armazenada em `backend/data/secret.key` (gerada automaticamente no primeiro início). Em produção, guarde bem esse arquivo junto com `users.json` e altere a senha padrão.

Para definições detalhadas da API, consulte os arquivos de rota em `backend/app/routers/`.

## Redefinir a senha

Se você esquecer a senha do administrador ou não conseguir entrar no painel web, pode redefini-la diretamente no servidor por meio de um script CLI (sem iniciar o backend):

```bash
cd backend

# Listar todas as contas
python reset_password.py --list

# Redefinir uma conta específica (digite a nova senha interativamente)
python reset_password.py admin

# Sem indicar a conta, o script solicita a escolha
python reset_password.py
```

O script lê/grava diretamente `backend/data/users.json`, oculta a digitação da senha e, após a redefinição, remove automaticamente a marca «trocar senha no primeiro login». A nova senha deve ter pelo menos 6 caracteres.

## Configuração

A configuração de proxy do servidor de desenvolvimento do frontend está em `frontend/vite.config.js`; por padrão, ela encaminha `/api` e o WebSocket para `http://localhost:8000`:

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

## Contribuições

Issues e pull requests são bem-vindos.

## License

AGPLv3