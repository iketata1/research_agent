"""Tests de la generation et de l'export du rapport hebdomadaire."""

from pathlib import Path

from research_agent.llm.client import Usage
from research_agent.models import Theme
from research_agent.reporting.aggregator import ReportItem, WeeklyReportData
from research_agent.reporting.generator import (
    build_report,
    report_filename,
    save_weekly_report,
)


def _item(theme=Theme.RESEARCH, score=88, title="Modele moisissure"):
    return ReportItem(
        id="x",
        source="openalex",
        title=title,
        url="https://openalex.org/W1",
        score=score,
        theme=theme,
        summary=["impact 1", "impact 2", "impact 3"],
    )


def _data(groups=None, start="2026-09-03", end="2026-09-10"):
    if groups is None:
        groups = {"research": [_item()]}
    return WeeklyReportData(period_start=start, period_end=end, groups=groups)


# --- Nom de fichier ----------------------------------------------------------


def test_filename_based_on_period():
    assert report_filename(_data(end="2026-09-10")) == "weekly_report_2026-09-10.md"


# --- Contenu du rapport ------------------------------------------------------


def test_build_report_includes_sections_and_footer():
    usage = Usage(input_tokens=1000, output_tokens=500, requests=3, cost=0.0021)
    report = build_report(_data(), usage=usage)
    assert "RESEARCH" in report
    assert "Modele moisissure" in report
    # Pied de page : metadonnees de run.
    assert "Genere le" in report
    assert "Items retenus : 1" in report
    assert "1500 tokens" in report
    assert "0.0021" in report


def test_build_report_without_usage():
    report = build_report(_data(), usage=None)
    assert "Items retenus" in report
    assert "LLM :" not in report  # pas de bloc LLM si usage absent


# --- Export fichier (tmp_path) -----------------------------------------------


def test_save_creates_markdown_file(tmp_path):
    path = save_weekly_report(output_dir=str(tmp_path), data=_data())
    file_path = Path(path)
    assert file_path.exists()
    assert file_path.name == "weekly_report_2026-09-10.md"
    assert file_path.suffix == ".md"


def test_saved_file_is_utf8_and_has_sections(tmp_path):
    groups = {
        "research": [_item(Theme.RESEARCH, 90, "Etude")],
        "opportunity": [_item(Theme.OPPORTUNITY, 85, "Tender")],
        "legal": [_item(Theme.LEGAL, 70, "Arret")],
    }
    path = save_weekly_report(output_dir=str(tmp_path), data=_data(groups))
    content = Path(path).read_text(encoding="utf-8")
    assert "RESEARCH" in content
    assert "OPPORTUNITY" in content
    assert "LEGAL" in content
    # Emoji present -> encodage UTF-8 correct.
    assert "\U0001F4CA" in content  # 📊 du titre


def test_save_creates_output_dir_if_missing(tmp_path):
    nested = tmp_path / "reports" / "weekly"
    path = save_weekly_report(output_dir=str(nested), data=_data())
    assert Path(path).exists()
    assert nested.is_dir()


def test_save_empty_report(tmp_path):
    path = save_weekly_report(output_dir=str(tmp_path), data=_data(groups={}))
    content = Path(path).read_text(encoding="utf-8")
    assert "Aucun element pertinent" in content
    assert "Items retenus : 0" in content
