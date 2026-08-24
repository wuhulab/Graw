# Graw

Un panneau d'administration de serveur basé sur le Web, avec une interface inspirée d'un système d'exploitation. Le frontend utilise Vue 3 + Vite ; le backend FastAPI. Il offre la surveillance système en temps réel, la gestion Docker, des processus et des fichiers, un terminal Web et des notes.

## Comment l'installer ?

docker run -d -p 8041:8000 --name graw-panel shunx/graw:latest

Remplace `8041` par ton port. Graw utilise un encapsuleur unifié sous Docker : un seul port suffit, aucune configuration multiple n'est nécessaire.

## Fonctionnalités

- **Système de comptes et de droits** — authentification JWT, rôles (administrateur / utilisateur standard), gestion des comptes, changement de mot de passe forcé. Après connexion, toutes les API protégées exigent `Authorization: Bearer <token>`
- **Interface type bureau** — applications fenêtrées, barre des tâches, raccourcis, glisser-déposer, maximiser/minimiser
- **Surveillance en temps réel** — CPU, mémoire, disque, réseau et charge, poussés en temps réel par WebSocket avec graphes
- **Gestion de sites web** — CRUD des hôtes virtuels Nginx / Apache, arrêt/démarrage, génération et consultation de la configuration
- **Gestion de bases de données** — connexions MySQL / MariaDB / Redis, exploration des BD/tableaux, exécution de commandes SQL / Redis
- **Tâches planifiées** — gestion des expressions Cron (encapsulation de Linux crontab / Windows schtasks)
- **Pare-feu** — règles de ports et listes d'IP autorisées/bloquées (iptables / netsh)
- **Certificats SSL** — import de certificats personnalisés et demande Let's Encrypt (certbot)
- **Centre de journaux** — consultation et vidage des journaux système, sites et panneau
- **Gestion Docker** — consulter conteneurs et images, démarrer, arrêter, voir les journaux, etc.
- **Gestion des processus** — liste et détails des processus en cours
- **Gestion des fichiers** — parcourir les répertoires, téléverser/télécharger, modifier les droits, compresser/extraire, copier et renommer
- **Terminal Web** — terminal dans le navigateur basé sur xterm.js pour piloter le serveur directement (WebSocket authentifié par `?token=`)
- **Notes** — noter et consulter des notes système

## Pile technique

| Couche | Technologies |
|------|------|
| Frontend | Vue 3, Vite, Axios, ECharts, vue-echarts, xterm.js |
| Backend | Python, FastAPI, Uvicorn, psutil, Docker SDK |
| Communication | REST API + WebSocket |

## Structure des répertoires

```
Graw/
├── frontend/          # Frontend Vue 3
│   ├── src/
│   │   ├── components/     # composants bureau, fenêtres, barre des tâches, cartes
│   │   ├── api.js          # encapsuleur de l'API backend
│   │   └── App.vue         # composant racine (bureau)
│   ├── package.json
│   └── vite.config.js
├── backend/           # Backend FastAPI
│   ├── app/
│   │   ├── main.py         # point d'entrée de l'application
│   │   └── routers/        # routes par module (system, docker, process, files, terminal, notes)
│   ├── api/                # routes rétro-compatibles (référençables directement)
│   └── requirements.txt
├── start.bat          # lancement en un clic sous Windows
├── start.sh           # lancement en un clic sous Linux / macOS
└── README.md
```

## Prise en main

### Prérequis

- Python 3.8+
- Node.js 16+
- (Optionnel) moteur Docker, requis pour la gestion Docker

### Démarrage manuel

**1. Démarrer le backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  
# Windows: .venv\Scripts\activate
pip install -r requirements.txt # première fois
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Ou, si vous développez :
py start.py

```

**2. Démarrer le frontend**

```bash
cd frontend
pnpm install
pnpm run dev
```

### Build de production

Le build de production du frontend est généré dans `frontend/dist` ; le backend détecte et monte automatiquement ce répertoire comme ressources statiques :

```bash
cd frontend
npm run build
```

Lancez ensuite le backend et accédez à l'application complète sur `http://localhost:8000`.

## Vue d'ensemble de l'API

| Module | Préfixe | Description |
|------|------|------|
| Auth | `/api/auth` | connexion, utilisateur courant, changement de mot de passe, gestion des utilisateurs (admin) |
| System | `/api/system` | CPU, mémoire, disque, réseau, charge, flux WebSocket en temps réel |
| Sites | `/api/sites` | gestion des hôtes virtuels (Nginx/Apache) |
| Databases | `/api/databases` | gestion des connexions et requêtes MySQL/MariaDB/Redis |
| Cron | `/api/cron` | tâches planifiées (Linux crontab / Windows schtasks) |
| Firewall | `/api/firewall` | règles du pare-feu par ports et IP |
| SSL | `/api/ssl` | import de certificats et demande Let's Encrypt |
| Logs | `/api/logs` | consultation et vidage des journaux |
| Docker | `/api/docker` | gestion des conteneurs et images |
| Process | `/api/process` | liste des processus et détails |
| Files | `/api/files` | navigation, transfert, droits, compression/extra |
| Terminal | `/api/terminal` | sessions terminal WebSocket (auth par `?token=`) |
| Notes | `/api/notes` | CRUD des notes |

Sauf `/api/auth/login` et `/api/health`, tous les endpoints exigent l'en-tête `Authorization: Bearer <token>`.

## Compte par défaut

Au premier démarrage, une entrée est créée automatiquement dans `backend/data/users.json` :

- Compte : `admin`
- Mot de passe : `admin123`
- Statut : changement de mot de passe forcé après la première connexion

La clé de signature est stockée dans `backend/data/secret.key` (générée automatiquement au premier démarrage). En production, conservez soigneusement ce fichier ainsi que `users.json` et modifiez le mot de passe par défaut.

Pour les définitions détaillées de l'API, consultez les fichiers de routes sous `backend/app/routers/`.

## Réinitialiser le mot de passe

Si vous oubliez le mot de passe administrateur ou ne pouvez pas vous connecter au panneau Web, vous pouvez réinitialiser le mot de passe directement sur le serveur via un script CLI (sans démarrer le backend) :

```bash
cd backend

# Lister tous les comptes
python reset_password.py --list

# Réinitialiser un compte précis (saisie interactive du nouveau mot de passe)
python reset_password.py admin

# Sans compte, le script invite à choisir
python reset_password.py
```

Le script lit/écrit directement `backend/data/users.json`, masque la saisie du mot de passe et, après la réinitialisation, supprime automatiquement le drapeau « changer le mot de passe à la première connexion ». Le nouveau mot de passe doit comporter au moins 6 caractères.

## Configuration

La configuration du proxy du serveur de développement du frontend se trouve dans `frontend/vite.config.js` ; par défaut, il redirige `/api` et le WebSocket vers `http://localhost:8000` :

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

## Contribution

Les issues et pull requests sont bienvenues.

## License

AGPLv3