"""Tests du rendu du rapport hebdomadaire (blocs thematiques, priorites, vide)."""

from research_agent.models import Theme
from research_agent.reporting.aggregator import ReportItem, WeeklyReportData
from research_agent.reporting.renderer import (
    priority_label,
    render_markdown,
)


def _item(theme, score, title="Titre", summary=None, url="https://example.org/a"):
    return ReportItem(
        id="x",
        source="test",
        title=title,
        url=url,
        score=score,
        theme=theme,
        summary=summary or ["ligne 1", "ligne 2", "ligne 3"],
    )


def _data(groups, start="2026-09-03", end="2026-09-10"):
    return WeeklyReportData(period_start=start, period_end=end, groups=groups)


# --- Badges de priorite ------------------------------------------------------


def test_high_score_opportunity_is_act():
    assert priority_label(_item(Theme.OPPORTUNITY, 90)) == "ACT"


def test_high_score_other_theme_is_high():
    assert priority_label(_item(Theme.RESEARCH, 90)) == "HIGH"


def test_moderate_score_has_no_badge():
    assert priority_label(_item(Theme.RESEARCH, 50)) == ""


def test_low_score_opportunity_not_act():
    # Une opportunite a faible score ne merite pas d'action immediate.
    assert priority_label(_item(Theme.OPPORTUNITY, 40)) == ""


# --- Blocs thematiques -------------------------------------------------------


def test_renders_all_theme_blocks():
    data = _data(
        {
            "research": [_item(Theme.RESEARCH, 80, title="Etude")],
            "opportunity": [_item(Theme.OPPORTUNITY, 85, title="Tender")],
            "legal": [_item(Theme.LEGAL, 70, title="Arret")],
            "technology": [_item(Theme.TECHNOLOGY, 60, title="Capteur")],
        }
    )
    md = render_markdown(data)
    assert "RESEARCH" in md
    assert "OPPORTUNITY" in md
    assert "LEGAL" in md
    assert "TECHNOLOGY" in md


def test_render_includes_title_period_summary_score_link():
    data = _data(
        {"research": [_item(Theme.RESEARCH, 88, title="Modele moisissure",
                            summary=["impact 1", "impact 2", "impact 3"],
                            url="https://openalex.org/W1")]}
    )
    md = render_markdown(data, title="MON RAPPORT")
    assert "MON RAPPORT" in md
    assert "2026-09-03" in md and "2026-09-10" in md
    assert "Modele moisissure" in md
    assert "impact 1" in md
    assert "score 88" in md
    assert "https://openalex.org/W1" in md


def test_act_badge_appears_in_render():
    data = _data({"opportunity": [_item(Theme.OPPORTUNITY, 90, title="Tender urgent")]})
    md = render_markdown(data)
    assert "ACT" in md


def test_high_badge_appears_in_render():
    data = _data({"research": [_item(Theme.RESEARCH, 90, title="Etude cle")]})
    md = render_markdown(data)
    assert "HIGH" in md


# --- Cas limites -------------------------------------------------------------


def test_empty_report():
    data = _data({})
    md = render_markdown(data)
    assert "Aucun element pertinent" in md
    # Pas de section thematique.
    assert "## " not in md


def test_theme_section_ordering():
    data = _data(
        {
            "research": [_item(Theme.RESEARCH, 80)],
            "legal": [_item(Theme.LEGAL, 80)],
        }
    )
    md = render_markdown(data)
    assert md.index("RESEARCH") < md.index("LEGAL")
