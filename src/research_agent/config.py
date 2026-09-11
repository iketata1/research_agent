"""Configuration centralisee, typee et validee (Pydantic v2).

Deux origines de configuration, volontairement separees :

- `config/config.yaml` : parametres fonctionnels non secrets (mots-cles de veille,
  seuils, sources, canal de livraison). Versionnable via son gabarit `.example`.
- `.env` / variables d'environnement : secrets (cles API, tokens). Jamais versionne.

Le point d'entree public est `load_settings()`, qui charge les deux origines,
les valide et retourne un objet `Settings` unique.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Chemins du projet -------------------------------------------------------

# Racine du projet : .../research-intelligence-agent
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


# --- Modeles pour le fichier YAML (parametres fonctionnels) ------------------


class Keywords(BaseModel):
    """Mots-cles de pre-filtrage, par langue (NL / EN / DE)."""

    nl: List[str] = Field(default_factory=list)
    en: List[str] = Field(default_factory=list)
    de: List[str] = Field(default_factory=list)

    def all(self) -> List[str]:
        """Retourne tous les mots-cles, toutes langues confondues (minuscules)."""
        return [kw.lower() for kw in (self.nl + self.en + self.de)]


class SourceConfig(BaseModel):
    """Parametres communs a une source de collecte.

    Les champs optionnels couvrent les differents types de source :
    `query` pour les APIs interrogeables, `feed_url` pour les flux RSS,
    `max_results` pour plafonner le volume collecte.
    """

    enabled: bool = True
    query: Optional[str] = None
    feed_url: Optional[str] = None
    max_results: int = Field(default=50, ge=1, le=1000)


class TelegramConfig(BaseModel):
    """Parametres de livraison Telegram (non secrets ; le token est dans .env)."""

    chat_id: str = ""


class EmailConfig(BaseModel):
    """Parametres de livraison email (les identifiants SMTP sont dans .env)."""

    recipients: List[str] = Field(default_factory=list)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)


class DeliveryConfig(BaseModel):
    """Canal de livraison du rapport hebdomadaire."""

    channel: str = "telegram"
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, value: str) -> str:
        allowed = {"telegram", "email"}
        if value not in allowed:
            raise ValueError(
                f"canal de livraison invalide : {value!r} (attendu : {allowed})"
            )
        return value


class LLMConfig(BaseModel):
    """Parametres du fournisseur LLM (API compatible OpenAI : OpenAI, Mistral...).

    Les tarifs servent a estimer le cout ; ils s'expriment en euros (ou dollars)
    par million de tokens, valeurs a ajuster selon le fournisseur/modele choisi.
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    max_tokens: int = Field(default=512, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout: float = Field(default=30.0, gt=0.0)
    max_retries: int = Field(default=4, ge=0, le=10)
    # Tarifs par million de tokens (entree / sortie).
    price_per_1m_input: float = Field(default=0.15, ge=0.0)
    price_per_1m_output: float = Field(default=0.60, ge=0.0)


class PreFilterConfig(BaseModel):
    """Regles du pre-filtre deterministe (applique avant le LLM).

    - required : au moins un de ces termes DOIT etre present (si la liste est non
      vide). Sert a garantir l'ancrage sur le domaine metier.
    - optional : termes qui augmentent la pertinence ; si `required` est vide,
      la presence d'au moins un `optional` suffit a passer.
    - excluded : la presence d'un de ces termes provoque un rejet immediat.
    """

    required: List[str] = Field(default_factory=list)
    optional: List[str] = Field(default_factory=list)
    excluded: List[str] = Field(default_factory=list)


class DatabaseConfig(BaseModel):
    """Parametres de la base de connaissances (SQLite pour le MVP).

    Le chemin est resolu relativement a la racine du projet s'il n'est pas absolu.
    """

    path: str = "data/knowledge_base.db"

    def resolved_path(self) -> Path:
        """Retourne le chemin absolu du fichier SQLite."""
        p = Path(self.path)
        return p if p.is_absolute() else (PROJECT_ROOT / p)


class AppConfig(BaseModel):
    """Racine de la configuration fonctionnelle (issue du YAML)."""

    keywords: Keywords = Field(default_factory=Keywords)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    prefilter: PreFilterConfig = Field(default_factory=PreFilterConfig)
    relevance_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    sources: Dict[str, SourceConfig] = Field(default_factory=dict)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    @model_validator(mode="after")
    def _check_delivery_ready(self) -> "AppConfig":
        """Verifie la coherence minimale du canal de livraison choisi."""
        if self.delivery.channel == "email" and not self.delivery.email.recipients:
            raise ValueError(
                "canal 'email' selectionne mais aucun destinataire configure."
            )
        return self


# --- Modele pour les secrets (.env / variables d'environnement) --------------


class Secrets(BaseSettings):
    """Secrets charges depuis l'environnement et/ou le fichier `.env`.

    Les noms d'attributs correspondent aux variables d'environnement
    (insensibles a la casse). Toutes les valeurs sont optionnelles pour permettre
    un chargement partiel en developpement, mais `require()` peut etre appele
    lorsqu'un secret devient indispensable.
    """

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    llm_api_key: Optional[str] = None
    openalex_mailto: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_alert_chat_id: Optional[str] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None

    def require(self, name: str) -> str:
        """Retourne un secret obligatoire ou leve une erreur explicite."""
        value = getattr(self, name, None)
        if not value:
            raise ValueError(
                f"secret requis manquant : {name.upper()} "
                "(renseignez-le dans .env)."
            )
        return value


# --- Objet de configuration unifie -------------------------------------------


class Settings(BaseModel):
    """Configuration complete du projet : fonctionnelle (YAML) + secrets (.env)."""

    app: AppConfig
    secrets: Secrets


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration introuvable : {path}. "
            "Copiez config/config.example.yaml vers config/config.yaml."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Charge, valide et retourne la configuration complete.

    Args:
        config_path: chemin explicite du YAML. Par defaut `config/config.yaml`.

    Returns:
        Objet `Settings` valide (leve `ValidationError`/`ValueError` sinon).
    """
    path = config_path or DEFAULT_CONFIG_PATH
    app = AppConfig.model_validate(_read_yaml(path))
    secrets = Secrets()  # lit .env + variables d'environnement
    return Settings(app=app, secrets=secrets)
