"""Point d'entree CLI du Research Intelligence Agent.

Coordonne les cycles du pipeline via des sous-commandes :

- `daily`  : collecte + pre-filtre + scoring + classification + persistance + alertes ;
- `weekly` : agregation de la semaine + rapport + livraison Telegram ;
- `run`    : un cycle quotidien suivi du rapport hebdomadaire.

Exemples :
    python -m research_agent.main daily
    python -m research_agent.main weekly --executive
    python -m research_agent.main daily --since 2026-09-01
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Optional, Sequence

from research_agent.logging_config import get_logger
from research_agent.monitoring import guard, setup_monitoring
from research_agent.pipeline import run_daily, run_weekly

logger = get_logger(__name__)


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"date --since invalide : {value!r} ({exc})")


def build_parser() -> argparse.ArgumentParser:
    """Construit l'analyseur d'arguments de la CLI."""
    parser = argparse.ArgumentParser(
        prog="research-agent",
        description="Research Intelligence Agent — orchestration du pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_daily = sub.add_parser("daily", help="cycle quotidien (collecte + filtrage)")
    p_daily.add_argument("--since", help="borne temporelle basse (ISO, ex. 2026-09-01)")

    p_weekly = sub.add_parser("weekly", help="rapport hebdomadaire + livraison")
    p_weekly.add_argument(
        "--executive", action="store_true", help="envoyer le resume executif"
    )

    sub.add_parser("run", help="cycle quotidien puis rapport hebdomadaire")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Point d'entree CLI.

    Returns:
        Code de sortie (0 = succes).
    """
    setup_monitoring()
    args = build_parser().parse_args(argv)

    # Interception des exceptions non gerees : tout crash critique est journalise
    # et declenche une alerte Telegram avant de faire echouer le processus.
    with guard(args.command):
        if args.command == "daily":
            run_daily(since=_parse_since(args.since))
        elif args.command == "weekly":
            run_weekly(executive=args.executive)
        elif args.command == "run":
            run_daily()
            run_weekly()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
