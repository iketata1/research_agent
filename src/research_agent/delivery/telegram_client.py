"""Client Bot Telegram pour la diffusion des rapports.

Interagit avec l'API Bot Telegram (endpoint `sendMessage`) via httpx, en
utilisant un token de bot et un identifiant de chat (config / variables
d'environnement).

Gere la limite stricte de 4096 caracteres de Telegram par un decoupage
automatique (chunking) qui coupe de preference sur les sauts de ligne, afin de
ne pas casser la mise en forme Markdown au milieu d'une ligne. Les erreurs
(reseau, token invalide, reponse `ok: false`) sont converties en `DeliveryError`.
"""

from __future__ import annotations

from typing import List, Optional

import httpx

from research_agent.config import Secrets
from research_agent.exceptions import DeliveryError
from research_agent.logging_config import get_logger

logger = get_logger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Limite stricte de Telegram par message. On garde une petite marge de securite.
TELEGRAM_MAX_LEN = 4096
_CHUNK_LIMIT = 4000


def split_message(text: str, limit: int = _CHUNK_LIMIT) -> List[str]:
    """Decoupe un texte en morceaux <= `limit`, en coupant sur les sauts de ligne.

    On accumule les lignes tant que la limite le permet. Une ligne plus longue
    que la limite est elle-meme decoupee en tranches brutes.

    Args:
        text: texte a decouper.
        limit: taille maximale d'un morceau.

    Returns:
        La liste des morceaux (au moins un, sauf texte vide -> liste vide).
    """
    if not text:
        return []
    chunks: List[str] = []
    current = ""
    for line in text.split("\n"):
        # Ligne unitaire trop longue : on la tranche brutalement.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        # Ajout de la ligne au morceau courant (avec le saut de ligne).
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class TelegramClient:
    """Client d'envoi de messages via l'API Bot Telegram."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        timeout: float = 20.0,
    ) -> None:
        secrets = Secrets()
        self.token = token or secrets.telegram_bot_token
        self.chat_id = chat_id or secrets.telegram_chat_id
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "TelegramClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def send_message(
        self,
        text: str,
        parse_mode: str = "Markdown",
    ) -> int:
        """Envoie un message, decoupe automatiquement si > 4096 caracteres.

        Args:
            text: contenu du message.
            parse_mode: format Telegram ("Markdown" ou "HTML").

        Returns:
            Le nombre de morceaux effectivement envoyes.

        Raises:
            DeliveryError: si le token/chat_id manque, ou en cas d'echec d'envoi.
        """
        if not self.token:
            raise DeliveryError("token Telegram manquant (TELEGRAM_BOT_TOKEN).")
        if not self.chat_id:
            raise DeliveryError("chat_id Telegram manquant (TELEGRAM_CHAT_ID).")

        chunks = split_message(text)
        if not chunks:
            return 0

        url = TELEGRAM_API_URL.format(token=self.token)
        for index, chunk in enumerate(chunks):
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            self._post(url, payload, index, len(chunks))
        logger.info("Message Telegram envoye en %d morceau(x).", len(chunks))
        return len(chunks)

    def _post(self, url: str, payload: dict, index: int, total: int) -> None:
        """Poste un morceau et verifie la reponse de l'API Telegram."""
        try:
            response = self.client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise DeliveryError(
                f"echec envoi Telegram (morceau {index + 1}/{total}) : {exc}"
            ) from exc
        except ValueError as exc:  # JSON illisible
            raise DeliveryError(f"reponse Telegram illisible : {exc}") from exc

        if not data.get("ok", False):
            description = data.get("description", "erreur inconnue")
            raise DeliveryError(f"Telegram a rejete le message : {description}")
