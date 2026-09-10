"""Connecteur factice pour illustrer et tester le contrat `BaseConnector`.

`MockConnector` ne fait aucun appel reseau : il renvoie une liste fixe de
`RawItem` (ou peut simuler une panne) afin de valider l'interface et le
comportement d'isolation des erreurs de `collect()`.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from research_agent.collectors.base import BaseConnector
from research_agent.exceptions import ConnectorError
from research_agent.models import RawItem


class MockConnector(BaseConnector):
    """Connecteur de demonstration.

    Args:
        items: items a renvoyer par `fetch()`. Si None, un item d'exemple est cree.
        fail: si True, `fetch()` leve une `ConnectorError` (pour tester l'isolation).
    """

    name = "mock"

    def __init__(
        self,
        items: Optional[List[RawItem]] = None,
        fail: bool = False,
    ) -> None:
        super().__init__()
        self._items = items
        self._fail = fail

    def fetch(self, since: datetime) -> List[RawItem]:
        if self._fail:
            raise ConnectorError("panne simulee", source=self.name)
        if self._items is not None:
            return self._items
        return [
            RawItem(
                source=self.name,
                title="Exemple d'item factice",
                url="https://example.org/mock/1",
                raw_text="Contenu de demonstration sur l'humidite et la moisissure.",
                language="fr",
            )
        ]
