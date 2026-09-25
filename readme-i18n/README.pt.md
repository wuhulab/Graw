# Graw

Um painel de administração de servidores baseado na web, com um design de interação «estilo sistema operacional de desktop» (janelas, barra de tarefas, atalhos na área de trabalho) e um modo de painel padrão no estilo 1Panel integrado. O frontend usa Vue 3 + Vite e o backend, FastAPI.

Além da máquina local, o Graw pode integrar outras máquinas como «nós filhos» em um único painel por meio de um **túnel Agent + chaves de acesso emparelhadas**: troque de máquina em um só lugar e administre contêineres, sites, arquivos, terminal e firewall de vários servidores.

### README em vários idiomas

[Chinês simplificado](../README.md) ·
[Chinês tradicional](./README.zh-TW.md) ·
[Inglês](./README.en.md) ·
[Japonês](./README.ja.md) ·
[Coreano](./README.ko.md) ·
[Russo](./README.ru.md) ·
[Espanhol](./README.es.md) ·
[Francês](./README.fr.md) ·
[Alemão](./README.de.md) ·
[Português](./README.pt.md) ·
[Esperanto](./README.eo.md)


## Links relacionados

| Projeto | Endereço |
|------|------|
| Repositório do código | <https://github.com/wuhulab/Graw> |
| Receitas da loja de aplicativos | <https://github.com/wuhulab/Graw-app-store> |
| Imagem Docker | <https://hub.docker.com/r/shunx/graw> |
| Site oficial | <https://graw.shunx.top/> |
| Relatar problemas | <https://github.com/wuhulab/Graw/issues> |
| Apoiar com doação | <https://afdian.com/a/shunianssy> |

## Como baixar?

O Graw roda como contêiner, mas **todas as suas operações de gerenciamento (contêineres/imagens Docker, instalação pela loja de aplicativos, terminal web, processos/firewall, configuração de sites etc.) precisam atuar no host**. Por isso **não basta** subir o contêiner de forma simples com `-p porta:8000`: é preciso iniciá-lo no «modo host completo» descrito abaixo, de modo que o contêiner acesse o Docker do host (socket) e a raiz do host (`/host`) e tenha privilégios de nível de host (`privileged` + `pid host`).

**Opção 1: servidor Linux (recomendada; rede host para escutar diretamente a porta 8000 do host)**

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

Após iniciar, acesse `http://<IP-do-servidor>:8000`.

**Opção 2: rede bridge (porta de acesso personalizada, por exemplo 8041)**

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

Após iniciar, acesse `http://<IP-do-servidor>:8041`.

**Opção 3: Docker Compose (o repositório já traz a orquestração com privilégios elevados)**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

Significado de cada parâmetro (todos necessários para o painel gerenciar o host por completo):

- `--privileged`: concede ao contêiner todas as capacidades do kernel; sem isso, `chroot /host`, iptables/firewall, montagens etc. não funcionam dentro do contêiner.
- `--pid host`: compartilha o namespace de processos do host; só assim o gerenciamento de processos e o monitoramento do sistema enxergam todos os processos do host.
- `--network host`: usa a rede do host e o painel escuta diretamente a porta 8000 do host (na opção 2 usa-se `-p mapeamento de portas`).
- `-v /:/host:rslave` + `HOST_ROOT=/host`: monta a raiz do host em `/host` dentro do contêiner, e o painel opera arquivos e comandos do host por meio de `chroot /host` (nginx/certbot/crontab etc.).
- `-v /var/run/docker.sock:/var/run/docker.sock`: conecta ao motor Docker do host (gerenciamento de contêineres/imagens/logs).
- `/opt/graw/data` é o diretório de dados do painel (montado a partir do host); `GRAW_HOST_DATA=/opt/graw/data` informa onde está o arquivo docker-compose no host, requisito para a loja de aplicativos Docker concluir a instalação.

> ⚠️ **Aviso de segurança**: esse contêiner tem, na prática, capacidade de operar como root no host, por isso só é recomendado implantá-lo em ambientes confiáveis. **Altere a senha padrão imediatamente** após o primeiro login e proteja bem os arquivos de credenciais em `backend/data/`.

## Recursos

- **Sistema de contas e permissões** — login baseado em JWT, papéis (administrador/usuário comum), gerenciamento de contas, troca forçada de senha, registro de logins, gerenciamento de sessões online (online definido pela validade do token e pela última atividade)
- **Dois modos de interface** — modo desktop (janelas/barra de tarefas/atalhos, com arrastar e maximizar/minimizar) e modo de painel padrão no estilo 1Panel (menu lateral agrupado + abas)
- **Gerenciamento de múltiplos nós** — o painel principal integra nós filhos por meio de túnel Agent + chaves de acesso emparelhadas; oferece implantação de chaves SSH, troca de host no nível da requisição e controle de capacidades dos nós filhos remotos
- **Monitoramento do sistema em tempo real** — CPU, memória, disco, rede e carga, com dados e gráficos em tempo real via WebSocket e consulta de métricas históricas
- **Gerenciamento de sites** — CRUD de hosts virtuais Nginx / OpenResty / Apache, iniciar/parar, gerar e visualizar configuração; convivência com 1Panel/OpenResty e descoberta automática de sites externos
- **WAF e melhorias de site** — firewall de aplicações web, pseudoestática/rewrite, cache e configurações avançadas do site, estatísticas do site
- **Gerenciamento de bancos de dados** — conexões MySQL / MariaDB / Redis / PostgreSQL / MongoDB, navegação por bancos e tabelas, execução de SQL / comandos Redis, análise de consultas lentas
- **Gerenciamento de Docker** — ver contêineres e imagens, iniciar, parar, logs e estatísticas de recursos; compatível com o formato de saída do docker e do podman
- **Loja de aplicativos** — instalação de aplicativos com um clique a partir de receitas YAML (instalar = `docker compose`), com edição personalizada do compose e logs de instalação
- **Gerenciamento de arquivos** — navegar em diretórios, enviar/baixar, alterar permissões, compactar/extrair, copiar e renomear; área de transferência no estilo Windows (Ctrl+C/V/Delete) e upload de pastas por arrastar e soltar
- **Lixeira** — arquivos excluídos por engano vão para a lixeira, com restauração e limpeza automática por dias (em todos os nós)
- **Terminal web** — terminal no navegador baseado em xterm.js para operar o servidor diretamente (WebSocket autenticado via `?token=`)
- **Tarefas agendadas / Firewall / SSL** — gerenciamento de expressões Cron (crontab / schtasks), portas e listas de permissão/bloqueio de IP (iptables / netsh, incluindo controle de entrada/saída das portas publicadas pelo Docker), upload de certificados e solicitação Let's Encrypt
- **Segurança e operação** — entrada segura ShunX, proteção antimanipulação de páginas web (alertas em tempo real por WS), regras de firewall unificadas, verificação de integridade, backup do painel, monitoramento de serviços, detecção de vencimento de certificados, central de notificações
- **Outros** — central de logs, gerenciamento de processos, notas, penetração de rede interna (Frp), armazenamento em rede, usuários FTP, gerenciamento de versões do PHP, caixa de ferramentas, protocolo aberto de plugins (GPOP)

## Pilha tecnológica

| Camada | Tecnologia |
|------|------|
| Frontend | Vue 3 (Composition API), Vite 5, Axios, ECharts / vue-echarts, xterm.js, vue-i18n |
| Backend | Python 3.11, FastAPI 0.115, Uvicorn, Pydantic 2, psutil, docker SDK |
| Comunicação | REST API (`/api/*`) + WebSocket (stream de monitoramento, terminal) |
| Implantação | build Docker em vários estágios (build do frontend em Node → runtime do backend em Python) |

## Estrutura de diretórios

```
Graw/
├── frontend/                 # Frontend Vue 3
│   ├── src/
│   │   ├── components/       # componentes de desktop, janelas, barra de tarefas e cartões
│   │   │   └── windows/      # uma janela independente por recurso (*Window.vue)
│   │   ├── store/            # estados reactive em singleton (auth / systemMetrics / docker ...)
│   │   ├── locales/          # idiomas do vue-i18n (22 no total)
│   │   └── App.vue           # componente raiz (alternância entre desktop e modo painel)
│   ├── vite.config.js        # proxy de desenvolvimento /api (inclui ws) → :8000
│   └── package.json
├── backend/                  # Backend FastAPI
│   ├── app/
│   │   ├── main.py           # ponto de entrada: registro de rotas, middleware e tarefas de fundo do lifespan
│   │   ├── auth.py           # dependências de autenticação JWT e criação de usuários
│   │   ├── agent_*.py        # autenticação / configuração / túnel proxy do Agent do nó filho
│   │   ├── node_manager.py   # contexto multinó e troca de host no nível da requisição
│   │   ├── hostfs.py         # camada de adaptação do sistema de arquivos do host (chroot /host)
│   │   ├── routers/          # rotas de cada módulo de negócio
│   │   └── data/             # dados em tempo de execução (gitignore, permissões restritas)
│   ├── test_*_unit.py        # testes unitários do pytest (test_*_e2e.py são casos ponta a ponta)
│   └── requirements.txt
├── app-store/                # receitas YAML e ícones da loja de aplicativos da comunidade
├── plugin-examples/          # exemplos do protocolo aberto de plugins (GPOP)
├── readme-i18n/              # README em vários idiomas
├── docs/                     # documentação complementar (protocolo de plugins etc.)
├── agent/                    # recursos e habilidades relacionados ao Agent do nó filho
├── Dockerfile                # build em vários estágios (frontend → runtime do backend)
├── docker-compose.yml        # orquestração com privilégios elevados para «gerenciar o host por completo»
├── start.sh / start.bat      # inicialização em um clique para desenvolvimento local (backend + frontend)
└── AGENTS.md                 # arquitetura e convenções de desenvolvimento (leitura obrigatória antes de mexer no código)
```

## Início rápido

### Requisitos

- Python 3.8+ (a imagem de produção usa 3.11)
- Node.js 16+
- (Opcional) motor Docker, para o recurso de gerenciamento de Docker

### Inicialização em um clique (desenvolvimento)

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

O script cria o ambiente virtual quando necessário, instala as dependências e sobe o backend e o frontend ao mesmo tempo.

### Inicialização manual

**1. Iniciar o backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # na primeira vez
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# ou use o script de desenvolvimento
python start.py
```

**2. Iniciar o frontend**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173, o Vite faz proxy de /api e ws para :8000
```

> No PowerShell/cmd do Windows não há suporte a `&&`; separe vários comandos com ponto e vírgula `;`.

### Build de produção

O build de produção do frontend é gerado em `frontend/dist`; o backend detecta e monta esse diretório automaticamente como recursos estáticos:

```bash
cd frontend
npm run build
```

Depois, basta iniciar o backend para acessar a aplicação completa em `http://localhost:8000` (o frontend é servido pelo backend na mesma origem, sem configuração de CORS). Também é possível compilar a imagem e implantá-la diretamente com o `Dockerfile` / `docker-compose.yml` do repositório.

## Visão geral da API

Todas as interfaces usam o prefixo `/api/*`; exceto `/api/auth/login` e `/api/health`, todas exigem o cabeçalho `Authorization: Bearer <token>`.

| Módulo | Prefixo | Descrição |
|------|------|------|
| Auth | `/api/auth` | Login, usuário atual, troca de senha, gerenciamento de usuários (administrador) |
| Agent | `/api/agent` | Autenticação entre nós filhos (troca de chaves emparelhadas por JWT) |
| System | `/api/system` | CPU, memória, disco, rede, carga e stream WebSocket em tempo real |
| Nodes | `/api/nodes` | Gerenciamento de múltiplos nós e troca do host gerenciado atual |
| Sites | `/api/sites` | Gerenciamento de hosts virtuais (Nginx / OpenResty / Apache) |
| WAF | `/api/waf` | Regras do firewall de aplicações web e proteção de sites |
| Databases | `/api/databases` | Conexões e consultas MySQL / MariaDB / Redis / PostgreSQL / MongoDB |
| Docker | `/api/docker` | Contêineres, imagens, volumes e edição de contêineres |
| Files | `/api/files` | Navegação, transferência, permissões, compactação e extração de arquivos |
| Recycle | `/api/recycle` | Lixeira (restauração, limpeza automática) |
| Terminal | `/api/terminal` | Sessões de terminal via WebSocket (autenticação via `?token=`) |
| App Store | `/api/appstore` | Instalação e gerenciamento de receitas da loja de aplicativos |
| Cron / Firewall / SSL | `/api/cron`, `/api/firewall`, `/api/ssl` | Tarefas agendadas / firewall / certificados |
| Plugins | `/api/plugins`, `/api/op` | Gerenciamento de plugins e API aberta de plugins (GPOP) |

A lista completa de rotas e os níveis de autenticação (`PROTECTED` / `ADMIN` / autenticação interna do endpoint) estão na seção 3 do [AGENTS.md](../AGENTS.md). A documentação da API fica desativada por padrão; para depurar, defina a variável de ambiente `GRAW_ENABLE_DOCS=1` e acesse `/docs`.

## Conta padrão

Na primeira inicialização, `backend/data/users.json` é criado automaticamente:

- Usuário: `admin`
- Senha: `admin123`
- Status: troca de senha obrigatória após o primeiro login

A chave de assinatura fica em `backend/data/secret.key` (gerada automaticamente na primeira inicialização). Em produção, guarde bem esse arquivo e o `users.json`, e altere a senha padrão.

## Redefinir a senha

Se você esquecer a senha do administrador ou não conseguir entrar no painel web, é possível redefini-la diretamente no servidor com um script de linha de comando (sem precisar iniciar o serviço backend):

```bash
cd backend

# listar todas as contas
python reset_password.py --list

# redefinir uma conta específica (a nova senha é digitada de forma interativa)
python reset_password.py admin

# sem informar conta, o script pedirá que você escolha
python reset_password.py
```

O script lê e grava diretamente `backend/data/users.json`, oculta a digitação da senha e, após a redefinição, limpa automaticamente o marcador «trocar a senha no primeiro login». A nova senha deve ter pelo menos 6 caracteres.

## Configuração

A configuração de proxy do servidor de desenvolvimento do frontend fica em `frontend/vite.config.js`; por padrão, encaminha `/api` e WebSocket para `http://localhost:8000`:

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

Variáveis de ambiente mais usadas:

| Variável | Descrição |
|------|------|
| `HOST_ROOT` | ponto de montagem da raiz do host dentro do contêiner (por exemplo `/host`); ativa o modo host |
| `GRAW_HOST_DATA` | caminho real do diretório `data` do painel no host; necessário para a instalação pela loja de aplicativos |
| `GRAW_ENABLE_DOCS` | quando definida como `1`, libera `/docs`, `/redoc` e `/openapi.json` |
| `GRAW_SESSION_ONLINE_SECONDS` | limite de inatividade (em segundos) para considerar a sessão online; padrão de 2 horas |
| `TZ` | fuso horário do contêiner, como `Asia/Shanghai` |

## Documentação do projeto

- [AGENTS.md](../AGENTS.md) — arquitetura, convenções e armadilhas comuns (**leia antes de alterar o código**)
- [CONTRIBUTING.md](../CONTRIBUTING.md) — guia de contribuição e acordo de licença do contribuidor (CLA)
- [SECURITY.md](../SECURITY.md) — fluxo para relatar problemas de segurança
- [CHANGELOG.md](../CHANGELOG.md) — histórico de mudanças de versão
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) — protocolo aberto de plugins (GPOP)
- [app-store/](../app-store/) — receitas da loja de aplicativos (YAML)
- [plugin-examples/](../plugin-examples/) — exemplos de plugins


## Contribuição

Issues e Pull Requests são bem-vindos; consulte [CONTRIBUTING.md](../CONTRIBUTING.md) para mais detalhes.

> **Acordo de licença do contribuidor (CLA)**: ao enviar código, documentação ou outro conteúdo a este projeto, você **concorda automaticamente** com o [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla), incluindo autorizar a **WuHuLaB** a manter o direito de usar sua contribuição para **fins comerciais** e de relicenciá-la/distribuí-la sob uma **licença fechada (proprietária)**.

## Doações

Se o Graw for útil para você, pague um café ao autor ☕

- Afdian: <https://afdian.com/a/shunianssy>
- Rainyun (patrocinador, servidores baratos): <https://www.rainyun.com/NjQwNjg5_>

## License

Este projeto é publicado como código aberto sob a [AGPLv3](../LICENSE).

De acordo com o [acordo de licença do contribuidor (CLA)](../CONTRIBUTING.md#7-贡献者许可协议cla), a WuHuLaB reserva-se o direito de usar este projeto (incluindo contribuições da comunidade) para **fins comerciais** e de relicenciá-lo ou distribuí-lo sob uma **licença fechada (proprietária)**.