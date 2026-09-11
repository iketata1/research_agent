# Guide de déploiement — Research Intelligence Agent

Ce guide décrit le déploiement de l'agent sur un VPS Linux (Ubuntu/Debian) pour
un fonctionnement autonome : collecte quotidienne, rapport hebdomadaire, et
alertes en temps réel sur Telegram.

## Sommaire

1. Prérequis
2. Récupération du code
3. Environnement Python et dépendances
4. Configuration (secrets et paramètres)
5. Permissions des dossiers de données et de logs
6. Initialisation de la base de données
7. Test manuel
8. Automatisation — option A : cron
9. Automatisation — option B : service systemd
10. Dépannage (troubleshooting)

---

## 1. Prérequis

- Un VPS sous Ubuntu 22.04+ ou Debian 12+ avec accès SSH.
- Python 3.10 ou supérieur (`python3 --version`).
- `git`, `python3-venv` et `pip` installés.
- Une clé API LLM (OpenAI ou Mistral).
- Un bot Telegram (token via @BotFather) et un chat_id destinataire.

Installation des paquets système de base :

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

## 2. Récupération du code

On déploie sous un utilisateur dédié non privilégié (recommandé pour la sécurité).

```bash
# Créer un utilisateur de service (optionnel mais recommandé)
sudo adduser --system --group --home /opt/research-agent researchagent

# Cloner le dépôt
sudo -u researchagent git clone https://github.com/iketata1/research_agent.git /opt/research-agent/app
cd /opt/research-agent/app
```

## 3. Environnement Python et dépendances

```bash
# Créer l'environnement virtuel
python3 -m venv .venv

# L'activer
source .venv/bin/activate

# Mettre pip à jour puis installer les dépendances
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Configuration (secrets et paramètres)

Deux fichiers, séparant secrets et paramètres fonctionnels.

### 4.1 Secrets — `.env`

```bash
cp .env.example .env
nano .env
```

Renseignez au minimum :

- `LLM_API_KEY` : votre clé API LLM.
- `TELEGRAM_BOT_TOKEN` : le token du bot.
- `TELEGRAM_CHAT_ID` : l'identifiant du chat destinataire.
- `OPENALEX_MAILTO` : votre email (recommandé, quotas OpenAlex plus stables).

Le fichier `.env` ne doit jamais être versionné ni partagé.

### 4.2 Paramètres — `config/config.yaml`

```bash
cp config/config.example.yaml config/config.yaml
nano config/config.yaml
```

Points clés à ajuster :

- `database.path` : chemin du fichier SQLite (par défaut `data/knowledge_base.db`).
- `relevance_threshold` : seuil de pertinence (0.0–1.0) pour conserver un item.
- `prefilter` : mots-clés requis / optionnels / exclus du pré-filtre gratuit.
- `sources` : sources actives et leurs paramètres (query, feed_url, max_results).
- `delivery.channel` : `telegram` ou `email`.

## 5. Permissions des dossiers de données et de logs

L'agent écrit dans `data/` (base SQLite) et `reports/` (rapports Markdown). En
option, on centralise les logs dans un dossier dédié.

```bash
# Dossiers de travail
mkdir -p data reports logs

# Attribuer la propriété à l'utilisateur de service
sudo chown -R researchagent:researchagent /opt/research-agent

# Permissions : lecture/écriture pour le propriétaire uniquement
chmod 750 data reports logs
chmod 600 .env            # secrets : lisible seulement par le propriétaire
```

Note SQLite : le mode WAL crée aussi `knowledge_base.db-wal` et `-shm` dans
`data/`. Le dossier `data/` doit donc être inscriptible, pas seulement le fichier.

## 6. Initialisation de la base de données

La base et son schéma sont créés automatiquement au premier run. Pour l'initialiser
explicitement :

```bash
source .venv/bin/activate
python -c "from research_agent.storage.database import initialize_database; initialize_database()"
```

## 7. Test manuel

Avant d'automatiser, vérifiez chaque cycle à la main.

```bash
source .venv/bin/activate

# Cycle quotidien (collecte + filtrage + persistance + alertes)
python -m research_agent.main daily

# Rapport hebdomadaire (agrégation + résumés + export + livraison Telegram)
python -m research_agent.main weekly
```

Vous devriez recevoir un message sur Telegram et voir un fichier dans `reports/`.

## 8. Automatisation — option A : cron

Approche simple : deux tâches cron appellent directement les commandes CLI.
C'est l'option recommandée si vous n'avez pas de processus à garder actif.

Éditez la crontab de l'utilisateur de service :

```bash
sudo -u researchagent crontab -e
```

Ajoutez (heures en fuseau du serveur — pensez à `TZ` si besoin) :

```cron
# Variables communes
APP=/opt/research-agent/app
PY=/opt/research-agent/app/.venv/bin/python

# Collecte quotidienne à 07:00
0 7 * * * cd $APP && $PY -m research_agent.main daily >> logs/daily.log 2>&1

# Rapport hebdomadaire le lundi à 08:00
0 8 * * 1 cd $APP && $PY -m research_agent.main weekly >> logs/weekly.log 2>&1
```

Vérifier que cron a bien pris les tâches :

```bash
sudo -u researchagent crontab -l
```

## 9. Automatisation — option B : service systemd

Approche alternative : le planificateur interne (`scheduler.py`) tourne en continu
comme un service géré par systemd (redémarrage automatique, logs journald).

Créez le fichier de service :

```bash
sudo nano /etc/systemd/system/research-agent.service
```

Contenu :

```ini
[Unit]
Description=Research Intelligence Agent (scheduler)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=researchagent
Group=researchagent
WorkingDirectory=/opt/research-agent/app
ExecStart=/opt/research-agent/app/.venv/bin/python -m research_agent.scheduler
Restart=on-failure
RestartSec=30
# Sécurité renforcée
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Activer et démarrer :

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now research-agent.service

# Vérifier l'état et suivre les logs
sudo systemctl status research-agent.service
sudo journalctl -u research-agent.service -f
```

Le scheduler déclenche le cycle quotidien (07:00) et le rapport hebdomadaire
(lundi 08:00), heures définies dans `src/research_agent/scheduler.py`.

Choix entre cron et systemd :
- cron : simple, aucune ressource consommée entre les runs. Recommandé.
- systemd : utile si vous voulez un processus résident et un contrôle fin
  (redémarrage auto, logs centralisés).

## 10. Dépannage (troubleshooting)

### Problèmes de connectivité réseau (sources ou APIs)

Symptôme : logs `ConnectorError` ou aucun item collecté.
- Le pipeline isole les pannes par source : une source injoignable n'interrompt
  pas les autres. Consultez `logs/daily.log` pour voir quelle source a échoué.
- Testez la connectivité : `curl -I https://api.openalex.org/works`.
- Vérifiez le pare-feu sortant du VPS (ports 443 autorisés).

### Token Telegram invalide ou expiré

Symptôme : `DeliveryError: Telegram a rejete le message : Unauthorized`.
- Vérifiez `TELEGRAM_BOT_TOKEN` dans `.env` (pas d'espace, pas de guillemets).
- Testez le token : `curl https://api.telegram.org/bot<TOKEN>/getMe`.
- Si le token a été régénéré via @BotFather, mettez à jour `.env`.
- `chat_id` inconnu : assurez-vous d'avoir écrit au bot au moins une fois, puis
  relisez `getUpdates`.

### Clé API LLM manquante ou invalide

Symptôme : `LLMError: cle API LLM manquante` ou erreurs HTTP 401.
- Renseignez `LLM_API_KEY` dans `.env`.
- Vérifiez que le modèle et `base_url` dans `config.yaml` (section `llm`)
  correspondent au fournisseur de votre clé.
- Erreurs 429 (rate limit) : le client réessaie automatiquement (backoff). Si
  elles persistent, réduisez `max_results` des sources ou augmentez l'intervalle.

### Erreurs d'écriture sur la base SQLite

Symptôme : `DatabaseError: erreur SQLite` (readonly / disk I/O / locked).
- Permissions : le dossier `data/` doit être inscriptible par l'utilisateur de
  service (`chown` + `chmod 750 data`). Le mode WAL écrit aussi `-wal`/`-shm`.
- Espace disque : vérifiez `df -h`.
- Base verrouillée : évitez d'exécuter deux runs simultanés. Le `busy_timeout`
  atténue les verrous courts, mais deux crons qui se chevauchent peuvent bloquer.
- Corruption (rare) : arrêtez l'agent, sauvegardez `data/`, puis recréez la base
  (voir section 6). Les runs passés restent tracés dans la table `runs`.

### Le run ne se déclenche pas (cron)

- Vérifiez que la crontab est celle du bon utilisateur (`crontab -l`).
- Cron utilise un PATH minimal : utilisez des chemins absolus (comme dans les
  exemples) et redirigez la sortie vers un fichier de log pour diagnostiquer.
- Fuseau horaire : `timedatectl` pour vérifier l'heure du serveur.

### Observabilité

Chaque exécution est tracée dans la table `runs` (type, horodatages, statut,
items collectés/filtrés, tokens et coût LLM). Pour inspecter les derniers runs :

```bash
sqlite3 data/knowledge_base.db \
  "SELECT run_type, status, items_collected, llm_cost, started_at FROM runs ORDER BY id DESC LIMIT 10;"
```
