# Graw

Un panneau d'administration de serveurs basé sur le Web, adoptant une conception d'interaction « type système d'exploitation de bureau » (fenêtres, barre des tâches, raccourcis du bureau) et intégrant également un mode panneau standard de style 1Panel. Le frontend utilise Vue 3 + Vite, le backend FastAPI.

Au-delà de la machine locale, Graw peut aussi intégrer d'autres hôtes dans un panneau unifié en tant que « nœuds enfants » grâce à un **tunnel Agent + des clés d'accès appairées** : changez d'hôte au même endroit et gérez les conteneurs, sites web, fichiers, terminaux et pare-feu de plusieurs serveurs.

### README multilingue

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


## Liens utiles

| Projet | Adresse |
|------|------|
| Dépôt source | <https://github.com/wuhulab/Graw> |
| Recettes du magasin d'applications | <https://github.com/wuhulab/Graw-app-store> |
| Image Docker | <https://hub.docker.com/r/shunx/graw> |
| Site officiel | <https://graw.shunx.top/> |
| Signaler un problème | <https://github.com/wuhulab/Graw/issues> |
| Soutenir par un don | <https://afdian.com/a/shunianssy> |

## Comment l'installer ?

Graw s'exécute sous forme de conteneur, mais **toutes ses opérations de gestion (conteneurs/images Docker, installation depuis le magasin d'applications, terminal Web, processus/pare-feu, configuration de sites web, etc.) doivent agir sur la machine hôte**. Vous **ne pouvez donc pas** simplement démarrer un conteneur nu avec un `-p port:8000` ; il faut le lancer selon le « mode hôte complet » décrit ci-dessous : le conteneur doit pouvoir accéder au Docker de l'hôte (socket), à la racine de l'hôte (`/host`) et disposer de privilèges au niveau de l'hôte (`privileged` + `pid host`).

**Option 1 : serveur Linux (recommandé, utilise le réseau host pour écouter directement sur le port 8000 de l'hôte)**

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

Une fois démarré, accédez à `http://<IP du serveur>:8000`.

**Option 2 : réseau Bridge (port d'accès personnalisé, par exemple 8041)**

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

Une fois démarré, accédez à `http://<IP du serveur>:8041`.

**Option 3 : Docker Compose (une orchestration à hauts privilèges est déjà fournie dans le dépôt)**

```bash
git clone https://github.com/wuhulab/Graw.git
cd Graw
docker compose up -d --build
```

Signification de chaque paramètre (nécessaires pour que le panneau gère complètement l'hôte) :

- `--privileged` : accorde au conteneur toutes les capacités du noyau ; sinon des opérations comme `chroot /host`, iptables/pare-feu ou les montages ne peuvent pas prendre effet dans le conteneur.
- `--pid host` : partage l'espace de noms des processus de l'hôte, afin que la gestion des processus et la surveillance système voient tous les processus de l'hôte.
- `--network host` : utilise le réseau de l'hôte, permettant au panneau d'écouter directement sur le port 8000 de l'hôte (l'option 2 utilise à la place le mappage de port `-p`).
- `-v /:/host:rslave` + `HOST_ROOT=/host` : monte la racine de l'hôte dans le conteneur sous `/host` ; le panneau agit alors sur les fichiers et commandes de l'hôte (nginx/certbot/crontab, etc.) via `chroot /host`.
- `-v /var/run/docker.sock:/var/run/docker.sock` : se connecte au moteur Docker de l'hôte (gestion des conteneurs/images/journaux).
- `/opt/graw/data` est le répertoire de données du panneau (lié à l'hôte) ; `GRAW_HOST_DATA=/opt/graw/data` indique à l'hôte où se trouve le fichier docker-compose, ce qui est nécessaire au magasin d'applications Docker pour mener à bien une installation.

> ⚠️ **Avertissement de sécurité** : le conteneur décrit ci-dessus possède en pratique une capacité d'opération de niveau root sur l'hôte ; il ne devrait donc être déployé que dans un environnement de confiance. Veuillez **changer immédiatement le mot de passe par défaut** après votre première connexion et protéger correctement les fichiers d'identification sous `backend/data/`.

## Fonctionnalités

- **Système de comptes et de permissions** — connexion utilisateur basée sur JWT, rôles (administrateur / utilisateur standard), gestion des comptes, changement de mot de passe forcé, journal de connexion, gestion des sessions en ligne (le statut en ligne est déterminé par la validité du token et l'heure de dernière activité)
- **Double forme d'interface** — mode type bureau (fenêtres / barre des tâches / raccourcis du bureau, avec glisser-déposer, agrandir/réduire) et mode panneau standard de style 1Panel (menu groupé dans la barre latérale + onglets multiples)
- **Gestion multi-nœuds** — le panneau principal prend en charge des nœuds enfants via un tunnel Agent + des clés d'accès appairées, avec déploiement de clés SSH, changement d'hôte au niveau de la requête et filtrage des capacités des nœuds enfants distants
- **Surveillance système en temps réel** — CPU, mémoire, disque, réseau et charge, poussés en temps réel via WebSocket avec données et graphiques, et prise en charge des requêtes d'indicateurs historiques
- **Gestion de sites web** — CRUD des hôtes virtuels Nginx / OpenResty / Apache, démarrage/arrêt, génération et consultation de la configuration ; coexistence possible avec 1Panel/OpenResty et découverte automatique des sites externes
- **WAF et améliorations de sites** — pare-feu applicatif Web (WAF), règles de réécriture (rewrite), cache et configuration d'amélioration de site, statistiques de site
- **Gestion de bases de données** — gestion des connexions MySQL / MariaDB / Redis / PostgreSQL / MongoDB, exploration des bases/tables, exécution de commandes SQL / Redis, analyse des requêtes lentes
- **Gestion Docker** — consultation, démarrage, arrêt, journaux et statistiques de ressources des conteneurs et images ; compatible avec les formats de sortie docker et podman
- **Magasin d'applications** — installation d'applications en un clic à partir de recettes YAML (l'installation correspond à `docker compose`), avec édition personnalisée du compose et journaux d'installation
- **Gestion de fichiers** — parcourir les répertoires, téléverser/télécharger, modifier les permissions, compresser/décompresser, copier/renommer ; presse-papiers à la Windows (Ctrl+C/V/Delete) et téléversement de dossiers par glisser-déposer
- **Corbeille** — les fichiers supprimés par erreur vont dans la corbeille, avec restauration et nettoyage automatique quotidien (sur tous les nœuds)
- **Terminal Web** — terminal dans le navigateur basé sur xterm.js pour agir directement sur le serveur (WebSocket authentifié via `?token=`)
- **Tâches planifiées / Pare-feu / SSL** — gestion des expressions Cron (crontab / schtasks), listes blanches/noires de ports et d'IP (iptables / netsh, y compris le contrôle entrant/sortant des ports publiés par Docker), téléversement de certificats et demande Let's Encrypt
- **Sécurité et exploitation** — entrée sécurisée ShunX, protection anti-falsification des pages web (alertes WebSocket en temps réel), règles de pare-feu unifiées, bilan de santé, sauvegarde du panneau, surveillance des services, détection d'expiration des certificats, centre de notifications
- **Autres** — centre de journaux, gestion des processus, notes, pénétration d'intranet (Frp), stockage réseau, utilisateurs FTP, gestion des versions PHP, boîte à outils, protocole ouvert de plugins (GPOP)

## Pile technique

| Couche | Technologie |
|------|------|
| Frontend | Vue 3 (Composition API), Vite 5, Axios, ECharts / vue-echarts, xterm.js, vue-i18n |
| Backend | Python 3.11, FastAPI 0.115, Uvicorn, Pydantic 2, psutil, docker SDK |
| Communication | REST API (`/api/*`) + WebSocket (flux de surveillance, terminal) |
| Déploiement | Build Docker multi-étapes (build Node du frontend → runtime Python du backend) |

## Structure des répertoires

```
Graw/
├── frontend/                 # frontend Vue 3
│   ├── src/
│   │   ├── components/       # composants bureau, fenêtres, barre des tâches, cartes
│   │   │   └── windows/      # un composant de fenêtre indépendant par fonction (*Window.vue)
│   │   ├── store/            # état singleton réactif (auth / systemMetrics / docker ...)
│   │   ├── locales/          # locales vue-i18n (22 au total)
│   │   └── App.vue           # composant racine (bascule environnement de bureau / mode panneau)
│   ├── vite.config.js        # proxy de développement /api (ws inclus) → :8000
│   └── package.json
├── backend/                  # backend FastAPI
│   ├── app/
│   │   ├── main.py           # point d'entrée de l'application : routes, middlewares, tâches de fond lifespan
│   │   ├── auth.py           # dépendances d'authentification JWT et création des utilisateurs
│   │   ├── agent_*.py        # authentification / configuration / proxy tunnel de l'Agent des nœuds enfants
│   │   ├── node_manager.py   # contexte multi-nœuds et changement d'hôte au niveau de la requête
│   │   ├── hostfs.py         # couche d'adaptation du système de fichiers de l'hôte (chroot /host)
│   │   ├── routers/          # routeurs métier par module
│   │   └── data/             # données d'exécution (gitignore, permissions restreintes)
│   ├── test_*_unit.py        # tests unitaires pytest (les test_*_e2e.py sont des cas de bout en bout)
│   └── requirements.txt
├── app-store/                # recettes YAML et icônes du magasin d'applications communautaire
├── plugin-examples/          # exemples du protocole ouvert de plugins (GPOP)
├── readme-i18n/              # README multilingues
├── docs/                     # documentation complémentaire (protocole de plugins, etc.)
├── agent/                    # ressources et compétences liées à l'Agent des nœuds enfants
├── Dockerfile                # build multi-étapes (build frontend → runtime backend)
├── docker-compose.yml        # orchestration à hauts privilèges « gestion complète de l'hôte »
├── start.sh / start.bat      # démarrage local en un clic (backend + frontend)
└── AGENTS.md                 # architecture du code et conventions de développement (à lire avant de modifier le code)
```

## Prise en main

### Prérequis

- Python 3.8+ (l'image de production est en 3.11)
- Node.js 16+
- (Optionnel) moteur Docker, pour la fonctionnalité de gestion Docker

### Démarrage en un clic (développement)

```bash
# Linux / macOS
./start.sh

# Windows
start.bat
```

Le script crée un environnement virtuel et installe les dépendances si nécessaire, puis lance en même temps le backend et le frontend.

### Démarrage manuel

**1. Démarrer le backend**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt  # la première fois
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# ou utiliser le script de démarrage de développement
python start.py
```

**2. Démarrer le frontend**

```bash
cd frontend
npm install
npm run dev                      # → http://localhost:5173, Vite proxifie /api et ws vers :8000
```

> Sous Windows, PowerShell/cmd ne prend pas en charge `&&` ; séparez plusieurs commandes par un point-virgule `;`.

### Build de production

Le build de production du frontend est généré dans `frontend/dist` ; le backend détecte et monte automatiquement ce répertoire comme ressources statiques :

```bash
cd frontend
npm run build
```

Lancez ensuite simplement le backend et accédez à l'application complète via `http://localhost:8000` (le frontend est servi en même origine par le backend, sans configuration CORS). Vous pouvez aussi construire et déployer l'image directement avec le `Dockerfile` / `docker-compose.yml` fourni dans le dépôt.

## Aperçu de l'API

Tous les points d'entrée sont préfixés par `/api/*` ; à l'exception de `/api/auth/login` et `/api/health`, tous exigent l'en-tête `Authorization: Bearer <token>`.

| Module | Préfixe | Description |
|------|------|------|
| Auth | `/api/auth` | connexion, utilisateur courant, changement de mot de passe, gestion des utilisateurs (admin) |
| Agent | `/api/agent` | authentification machine à machine des nœuds enfants (clé appairée échangée contre un JWT) |
| System | `/api/system` | CPU, mémoire, disque, réseau, charge, flux temps réel WebSocket |
| Nodes | `/api/nodes` | gestion multi-nœuds et changement de l'hôte géré courant |
| Sites | `/api/sites` | gestion des hôtes virtuels de sites (Nginx / OpenResty / Apache) |
| WAF | `/api/waf` | règles du pare-feu applicatif Web et protection des sites |
| Databases | `/api/databases` | connexions et requêtes MySQL / MariaDB / Redis / PostgreSQL / MongoDB |
| Docker | `/api/docker` | conteneurs, images, volumes et édition de conteneurs |
| Files | `/api/files` | navigation, transfert, permissions, compression/décompression de fichiers |
| Recycle | `/api/recycle` | corbeille (restauration, nettoyage automatique) |
| Terminal | `/api/terminal` | sessions de terminal WebSocket (authentification via `?token=`) |
| App Store | `/api/appstore` | installation et gestion des recettes du magasin d'applications |
| Cron / Firewall / SSL | `/api/cron`, `/api/firewall`, `/api/ssl` | tâches planifiées / pare-feu / certificats |
| Plugins | `/api/plugins`, `/api/op` | gestion des plugins et interface ouverte de plugins (GPOP) |

Pour la liste complète des routes et les niveaux d'authentification (`PROTECTED` / `ADMIN` / auto-authentification au niveau du point d'entrée), voir la section 3 de [AGENTS.md](../AGENTS.md). La documentation de l'API est désactivée par défaut ; pour le débogage, définissez la variable d'environnement `GRAW_ENABLE_DOCS=1` puis accédez à `/docs`.

## Compte par défaut

Au premier démarrage, une entrée est créée automatiquement dans `backend/data/users.json` :

- Compte : `admin`
- Mot de passe : `admin123`
- Statut : changement de mot de passe forcé après la première connexion

La clé de signature est conservée dans `backend/data/secret.key` (générée automatiquement au premier démarrage). En production, conservez soigneusement ce fichier ainsi que `users.json`, et modifiez le mot de passe par défaut.

## Réinitialiser le mot de passe

Si vous oubliez le mot de passe administrateur ou ne pouvez pas vous connecter au panneau Web, vous pouvez le réinitialiser directement sur le serveur via un script en ligne de commande (sans démarrer le service backend) :

```bash
cd backend

# Lister tous les comptes
python reset_password.py --list

# Réinitialiser un compte précis (saisie interactive du nouveau mot de passe)
python reset_password.py admin

# Sans préciser de compte, le script invite à en choisir un
python reset_password.py
```

Le script lit/écrit directement `backend/data/users.json`, masque la saisie du mot de passe et efface automatiquement après la réinitialisation le drapeau « changement de mot de passe obligatoire à la première connexion ». Le nouveau mot de passe doit comporter au moins 6 caractères.

## Configuration

La configuration du proxy du serveur de développement du frontend se trouve dans `frontend/vite.config.js` ; par défaut, elle redirige `/api` et WebSocket vers `http://localhost:8000` :

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

Variables d'environnement courantes :

| Variable | Description |
|------|------|
| `HOST_ROOT` | point de montage de la racine de l'hôte dans le conteneur (par ex. `/host`), active le mode hôte |
| `GRAW_HOST_DATA` | chemin réel du répertoire `data` du panneau sur l'hôte, requis pour l'installation depuis le magasin d'applications |
| `GRAW_ENABLE_DOCS` | réglé sur `1`, expose `/docs`, `/redoc`, `/openapi.json` |
| `GRAW_SESSION_ONLINE_SECONDS` | seuil d'inactivité (secondes) pour considérer une session en ligne ; par défaut 2 heures |
| `TZ` | fuseau horaire du conteneur, par ex. `Asia/Shanghai` |

## Documentation du projet

- [AGENTS.md](../AGENTS.md) — architecture, conventions et pièges courants (**à lire en entier avant de modifier le code**)
- [CONTRIBUTING.md](../CONTRIBUTING.md) — guide de contribution et accord de licence du contributeur (CLA)
- [SECURITY.md](../SECURITY.md) — procédure de signalement des problèmes de sécurité
- [CHANGELOG.md](../CHANGELOG.md) — journal des versions
- [docs/plugin-protocol.md](../docs/plugin-protocol.md) — protocole ouvert de plugins (GPOP)
- [app-store/](../app-store/) — recettes du magasin d'applications (YAML)
- [plugin-examples/](../plugin-examples/) — exemples de plugins


## Contribution

Les issues et pull requests sont les bienvenues ; voir [CONTRIBUTING.md](../CONTRIBUTING.md) pour plus de détails.

> **Accord de licence du contributeur (CLA)** : en soumettant du code, de la documentation ou tout autre contenu à ce projet, vous **acceptez par défaut** le [CLA](../CONTRIBUTING.md#7-贡献者许可协议cla), y compris l'autorisation donnée à **WuHuLaB** de conserver le droit d'utiliser votre contribution à des **fins commerciales** et de la sous-licencier/distribuer sous une **licence à code fermé (propriétaire)**.

## Faites un don

Si Graw vous a été utile, vous êtes invité à offrir un café à l'auteur ☕

- Afdian (爱发电) : <https://afdian.com/a/shunianssy>
- RainYun (雨云, sponsor, serveurs bon marché) : <https://www.rainyun.com/NjQwNjg5_>

## License

Ce projet est publié en open source sous [AGPLv3](../LICENSE).

Conformément à l'[accord de licence du contributeur (CLA)](../CONTRIBUTING.md#7-贡献者许可协议cla), WuHuLaB conserve le droit d'utiliser ce projet (y compris les contributions de la communauté) à des **fins commerciales** et de le sous-licencier ou de le distribuer sous une **licence à code fermé (propriétaire)**.