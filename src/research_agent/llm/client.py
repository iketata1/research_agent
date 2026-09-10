"""Client LLM robuste (API compatible OpenAI) avec retry et suivi des couts.

Ce wrapper appelle l'endpoint `chat/completions` d'une API compatible OpenAI
(OpenAI, Mistral, etc.) via httpx. Il fournit :

- une gestion des erreurs de production : rate limit (429), timeouts, erreurs
  reseau et reponses illisibles, toutes converties en `LLMError` ;
- une strategie de reessai avec backoff exponentiel + jitter, pour tolerer les
  micro-coupures et la saturation cote fournisseur ;
- un suivi des tokens (entree/sortie) et une estimation du cout cumule, pour
  eviter les factures surprises.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from research_agent.config import LLMConfig, Secrets
from research_agent.exceptions import LLMError
from research_agent.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Usage:
    """Suivi cumule des tokens et du cout estime."""

    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    cost: float = 0.0

    def add(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost += cost
        self.requests += 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "requests": self.requests,
            "cost": round(self.cost, 6),
        }


@dataclass
class LLMResponse:
    """Reponse d'une completion LLM."""

    text: str
    input_tokens: int
    output_tokens: int
    cost: float


# Codes HTTP consideres comme temporaires (a reessayer).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMClient:
    """Client synchrone pour une API de completion compatible OpenAI."""

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.config = config or LLMConfig()
        self.api_key = api_key or Secrets().llm_api_key
        self.usage = Usage()
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.config.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- Estimation du cout ---------------------------------------------------

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estime le cout d'une requete a partir des tokens et des tarifs config."""
        cost_in = input_tokens / 1_000_000 * self.config.price_per_1m_input
        cost_out = output_tokens / 1_000_000 * self.config.price_per_1m_output
        return cost_in + cost_out

    # --- Appel principal ------------------------------------------------------

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Envoie une requete de completion et renvoie la reponse detaillee.

        Args:
            prompt: message utilisateur.
            system: message systeme optionnel (instructions).
            temperature: temperature ; par defaut celle de la config.
            max_tokens: nombre max de tokens generes ; par defaut celui de la config.

        Returns:
            La reponse du LLM, avec tokens et cout.

        Raises:
            LLMError: si l'appel echoue definitivement (apres reessais) ou si la
                reponse est illisible / une cle API manque.
        """
        if not self.api_key:
            raise LLMError("cle API LLM manquante (renseignez LLM_API_KEY).")

        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        data = self._request_with_retry(payload)
        return self._parse_response(data)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Facade simple : envoie un prompt et renvoie uniquement le texte genere.

        Pratique lorsqu'on n'a besoin que de la reponse textuelle (le suivi des
        tokens/couts reste mis a jour en interne via `complete`).

        Args:
            prompt: message utilisateur.
            system_prompt: instructions systeme optionnelles.
            temperature: temperature ; par defaut celle de la config.
            max_tokens: nombre max de tokens generes ; par defaut celui de la config.

        Returns:
            Le texte de la reponse du LLM.

        Raises:
            LLMError: en cas d'echec (voir `complete`).
        """
        response = self.complete(
            prompt,
            system=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.text

    def _request_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Effectue l'appel HTTP avec reessais (backoff exponentiel + jitter)."""
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.client.post(url, json=payload, headers=headers)
                if response.status_code in _RETRYABLE_STATUS:
                    raise _RetryableError(
                        f"statut {response.status_code}", response.status_code
                    )
                response.raise_for_status()
                return response.json()
            except (_RetryableError, httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "Appel LLM echoue (tentative %d/%d) : %s. Nouvelle tentative dans %.2fs.",
                    attempt + 1,
                    self.config.max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
            except httpx.HTTPStatusError as exc:
                # Erreur non reessayable (4xx hors 429) : echec immediat.
                raise LLMError(
                    f"erreur HTTP LLM : {exc}", provider=self.config.model
                ) from exc

        raise LLMError(
            f"appel LLM echoue apres {self.config.max_retries + 1} tentative(s) : {last_error}",
            provider=self.config.model,
        )

    def _backoff_delay(self, attempt: int) -> float:
        """Delai d'attente : backoff exponentiel (base 0.5s) + jitter aleatoire."""
        base = 0.5 * (2 ** attempt)
        jitter = random.uniform(0, base * 0.25)
        return base + jitter

    def _parse_response(self, data: Dict[str, Any]) -> LLMResponse:
        """Extrait le texte et les tokens de la reponse, met a jour le suivi."""
        try:
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"reponse LLM illisible : {exc}", provider=self.config.model
            ) from exc

        cost = self.estimate_cost(input_tokens, output_tokens)
        self.usage.add(input_tokens, output_tokens, cost)
        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )


class _RetryableError(Exception):
    """Erreur interne signalant un statut HTTP temporaire (a reessayer)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
