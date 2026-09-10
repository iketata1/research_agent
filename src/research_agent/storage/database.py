"""Connexion et acces a la base de connaissances SQLite.

Ce module fournit :
- une classe `Database` encapsulant un fichier SQLite dont le chemin est
  configurable (via `AppConfig.database`) ;
- un gestionnaire de contexte `connection()` garantissant commit / rollback /
  fermeture, pour eviter toute fuite de connexion ;
- une fonction `initialize()` qui cree le dossier de destination et le fichier.

Choix pour la concurrence de base :
- mode journal WAL : autorise des lectures concurrentes pendant une ecriture ;
- `busy_timeout` : attend au lieu d'echouer immediatement si la base est verrouillee ;
- `check_same_thread=False` : autorise l'usage depuis differents threads
  (chaque appel de `connection()` ouvre et ferme sa propre connexion).

Les erreurs SQLite sont encapsulees dans `DatabaseError` pour rester coherentes
avec la hierarchie d'exceptions du projet.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

from research_agent.config import DatabaseConfig, load_settings
from research_agent.exceptions import DatabaseError
from research_agent.logging_config import get_logger
from research_agent.storage.migrations import run_migrations

logger = get_logger(__name__)

# Delai d'attente (ms) si la base est verrouillee par une autre connexion.
_BUSY_TIMEOUT_MS = 5000


class Database:
    """Encapsule un fichier SQLite et fournit des connexions sures.

    Attributes:
        path: chemin absolu du fichier SQLite.
    """

    def __init__(self, path: Union[str, Path]) -> None:
        # ":memory:" designe une base en memoire (utile pour les tests).
        self.is_memory = str(path) == ":memory:"
        self.path = path if self.is_memory else Path(path)
        # En mode memoire, on conserve une connexion unique persistante : sinon
        # la base disparaitrait a la fermeture de chaque connexion.
        self._mem_conn: Optional[sqlite3.Connection] = None

    @classmethod
    def from_config(cls, config: Optional[DatabaseConfig] = None) -> "Database":
        """Construit une `Database` a partir de la configuration du projet.

        Args:
            config: configuration base de donnees. Si absente, chargee depuis
                `config/config.yaml`.
        """
        if config is None:
            config = load_settings().app.database
        return cls(config.resolved_path())

    def initialize(self) -> None:
        """S'assure que le dossier, le fichier et le schema existent.

        Cree l'arborescence parente si necessaire, ouvre une connexion (ce qui
        cree le fichier) puis applique les migrations pour garantir le schema.
        """
        if not self.is_memory:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DatabaseError(
                    f"impossible de creer le dossier de base : {self.path.parent}"
                ) from exc
        with self.connection() as conn:
            version = run_migrations(conn)
        logger.info("Base de donnees initialisee : %s (schema v%d)", self.path, version)

    def _connect(self) -> sqlite3.Connection:
        """Ouvre une connexion SQLite configuree (pragmas).

        En mode memoire, une connexion unique persistante est reutilisee afin
        que les donnees survivent entre les appels de `connection()`.
        """
        if self.is_memory and self._mem_conn is not None:
            return self._mem_conn

        conn = sqlite3.connect(
            self.path,
            timeout=_BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        # Acces aux colonnes par nom (row["title"]).
        conn.row_factory = sqlite3.Row
        # Pragmas de robustesse et de concurrence.
        # WAL n'a pas de sens pour une base en memoire ; on ne l'active que sur fichier.
        if not self.is_memory:
            conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS};")

        if self.is_memory:
            self._mem_conn = conn
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Gestionnaire de contexte fournissant une connexion transactionnelle.

        Valide (commit) en sortie normale, annule (rollback) en cas d'exception,
        et ferme toujours la connexion.

        Yields:
            Une connexion SQLite ouverte.

        Raises:
            DatabaseError: en cas d'erreur SQLite.
        """
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = self._connect()
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            if conn is not None:
                conn.rollback()
            raise DatabaseError(f"erreur SQLite : {exc}") from exc
        finally:
            # En mode memoire, on garde la connexion unique ouverte (sinon la
            # base serait perdue). En mode fichier, on ferme systematiquement.
            if conn is not None and not self.is_memory:
                conn.close()

    def close(self) -> None:
        """Ferme la connexion memoire persistante, le cas echeant."""
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None


def initialize_database(config: Optional[DatabaseConfig] = None) -> Database:
    """Cree (si besoin) et retourne la base de connaissances.

    Args:
        config: configuration base de donnees ; chargee depuis le YAML si absente.

    Returns:
        Une instance `Database` prete a l'emploi.
    """
    db = Database.from_config(config)
    db.initialize()
    return db
