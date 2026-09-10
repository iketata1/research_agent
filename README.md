# Research Intelligence Agent

Agent autonome de veille pour **Intra-Air**. Il collecte chaque jour des données
issues de 6 sources gratuites, les filtre par pertinence via un LLM, les organise
par thème, les stocke dans une base consultable, et délivre un rapport structuré
chaque lundi (Telegram ou email).

## Domaine métier

Détection de moisissures / humidité dans le logement, monitoring par capteurs, et
obligations légales des bailleurs. Le filtrage de pertinence est ancré sur ce
domaine.

## Les 6 sources du MVP

| # | Source         | Domaine                    | Accès       |
|---|----------------|----------------------------|-------------|
| 1 | OpenAlex       | Recherche scientifique     | API REST    |
| 2 | TenderNed      | Marchés publics (NL)       | RSS + API   |
| 3 | TED (Europe)   | Marchés publics (EU)       | API         |
| 4 | Google News    | Signaux marché / clients   | RSS         |
| 5 | Aedes.nl       | Secteur logement (clients) | RSS         |
| 6 | Rechtspraak.nl | Jurisprudence (moisissures)| API         |

## Pipeline

```
collect  ->  normalize  ->  pre-filter  ->  llm enrich  ->  store  ->  report
```

## Structure du projet

```
research-intelligence-agent/
├── config/                 # Configuration (sources, mots-cles, destinataires)
│   └── config.example.yaml
├── data/                   # Base de donnees et donnees locales (non versionne)
├── src/
│   └── research_agent/
│       ├── collectors/     # Un connecteur par source
│       ├── processing/     # Normalisation, dedup, filtrage, LLM
│       ├── storage/        # Acces base de donnees
│       ├── reporting/      # Generation et envoi du rapport
│       ├── config.py       # Chargement de la config
│       ├── models.py       # Schema pivot (modele de donnees commun)
│       └── pipeline.py     # Orchestration du pipeline
├── tests/                  # Tests unitaires et d'integration
├── requirements.txt
├── .gitignore
└── README.md
```

## Prise en main

```bash
# 1. Creer et activer un environnement virtuel
python3 -m venv .venv
source .venv/bin/activate

# 2. Installer les dependances
pip install -r requirements.txt

# 3. Copier et adapter la configuration
cp config/config.example.yaml config/config.yaml

# 4. Lancer le pipeline (a venir)
python -m research_agent.pipeline
```

## Statut

Phase 1 — Environnement et fondations (en cours).
