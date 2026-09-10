"""Tests du formatage et de la diffusion Telegram du rapport."""

from unittest.mock import MagicMock

from research_agent.delivery.formatter import (
    format_executive_summary,
    format_full_report,
    send_weekly_report,
)
from research_agent.exceptions import DeliveryError
from research_agent.models import Theme
from research_agent.reporting.aggregator import ReportItem, WeeklyReportData


def _item(theme=Theme.RESEARCH, score=88, title="Modele moisissure", summary=None):
    return ReportItem(
        id="x",
        source="openalex",
        title=title,
        url="https://openalex.org/W1",
        score=score,
        theme=theme,
        summary=summary or ["impact 1", "impact 2", "impact 3"],
    )


def _data(groups=None, start="2026-09-03", end="2026-09-10"):
    if groups is None:
        groups = {"research": [_item()]}
    return WeeklyReportData(period_start=start, period_end=end, groups=groups)


# --- Rapport complet ---------------------------------------------------------


def test_full_report_includes_header_theme_score_summary_url():
    msg = format_full_report(_data())
    assert "INTRA-AIR RESEARCH INTELLIGENCE" in msg
    assert "2026-09-03" in msg and "2026-09-10" in msg
    assert "RESEARCH" in msg
    assert "Modele moisissure" in msg
    assert "(88)" in msg
    assert "impact 1" in msg
    assert "https://openalex.org/W1" in msg


def test_full_report_has_theme_emoji():
    msg = format_full_report(_data())
    assert "\U0001F52C" in msg  # microscope RESEARCH


def test_full_report_act_badge_for_opportunity():
    data = _data({"opportunity": [_item(Theme.OPPORTUNITY, 90, "Tender")]})
    msg = format_full_report(data)
    assert "ACT" in msg


def test_full_report_high_badge():
    msg = format_full_report(_data({"research": [_item(Theme.RESEARCH, 90)]}))
    assert "HIGH" in msg


def test_full_report_empty():
    msg = format_full_report(_data(groups={}))
    assert "Aucun element pertinent" in msg


# --- Resume executif ---------------------------------------------------------


def test_executive_summary_limits_top_n():
    items = [_item(Theme.RESEARCH, 90 - i, title=f"T{i}") for i in range(5)]
    data = _data({"research": items})
    msg = format_executive_summary(data, top_n=2)
    assert "T0" in msg and "T1" in msg
    assert "T2" not in msg  # au-dela du top 2


def test_executive_summary_omits_full_summary_lines():
    data = _data({"research": [_item(summary=["ligne detaillee"])]})
    msg = format_executive_summary(data)
    # Le resume executif n'inclut pas les lignes de resume detaillees.
    assert "ligne detaillee" not in msg


# --- Diffusion + fallback ----------------------------------------------------


def test_send_weekly_report_success():
    client = MagicMock()
    client.send_message.return_value = 1
    assert send_weekly_report(_data(), client=client) is True
    client.send_message.assert_called_once()


def test_send_executive_uses_summary():
    client = MagicMock()
    client.send_message.return_value = 1
    send_weekly_report(_data(), client=client, executive=True)
    sent = client.send_message.call_args[0][0]
    # Le message envoye ne contient pas les lignes de resume detaillees.
    assert "impact 1" not in sent


def test_send_weekly_report_fallback_on_failure():
    """Un echec de livraison est journalise, non propage : renvoie False."""
    client = MagicMock()
    client.send_message.side_effect = DeliveryError("token invalide")
    result = send_weekly_report(_data(), client=client)
    assert result is False  # pipeline non bloque
