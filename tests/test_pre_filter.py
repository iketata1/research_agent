"""Tests du pre-filtre deterministe par mots-cles."""

from research_agent.config import PreFilterConfig
from research_agent.filtering.pre_filter import (
    evaluate,
    pre_filter,
    pre_filter_item,
)
from research_agent.models import RawItem


def _item(title="Titre", raw_text="", url="https://example.org/a"):
    return RawItem(source="test", title=title, url=url, raw_text=raw_text)


# --- Exclusions --------------------------------------------------------------


def test_excluded_term_rejects():
    cfg = PreFilterConfig(excluded=["publicite"])
    item = _item(title="Grande publicite pour un produit")
    result = evaluate(item, cfg)
    assert result.passed is False
    assert "exclu" in result.reason


def test_exclusion_has_priority_over_required():
    cfg = PreFilterConfig(required=["schimmel"], excluded=["sponsor"])
    item = _item(title="schimmel artikel", raw_text="met sponsor vermelding")
    assert evaluate(item, cfg).passed is False


# --- Termes requis -----------------------------------------------------------


def test_required_term_present_passes():
    cfg = PreFilterConfig(required=["schimmel", "vocht"])
    item = _item(title="Vocht en schimmel in woning")
    result = evaluate(item, cfg)
    assert result.passed is True
    assert result.matched in {"schimmel", "vocht"}


def test_required_absent_rejects():
    cfg = PreFilterConfig(required=["schimmel"])
    item = _item(title="Wegenbouw en asfalt")
    result = evaluate(item, cfg)
    assert result.passed is False
    assert "requis" in result.reason


# --- Termes optionnels -------------------------------------------------------


def test_optional_fallback_when_no_required():
    cfg = PreFilterConfig(optional=["ventilation", "humidity"])
    assert evaluate(_item(title="About humidity"), cfg).passed is True
    assert evaluate(_item(title="About roads"), cfg).passed is False


def test_no_rules_passes_everything():
    cfg = PreFilterConfig()
    assert evaluate(_item(title="n'importe quoi"), cfg).passed is True


# --- Recherche sur titre + texte + casse -------------------------------------


def test_match_is_case_insensitive():
    cfg = PreFilterConfig(required=["Schimmel"])
    assert evaluate(_item(title="SCHIMMEL probleem"), cfg).passed is True


def test_match_searches_raw_text_too():
    cfg = PreFilterConfig(required=["ventilation"])
    item = _item(title="Titre neutre", raw_text="a study on ventilation systems")
    assert evaluate(item, cfg).passed is True


# --- Annotation des metadonnees ----------------------------------------------


def test_metadata_annotated_on_pass():
    cfg = PreFilterConfig(required=["schimmel"])
    item = _item(title="schimmel")
    assert pre_filter_item(item, cfg) is True
    assert item.metadata["passed_prefilter"] is True
    assert item.metadata["prefilter_matched"] == "schimmel"


def test_metadata_annotated_on_reject():
    cfg = PreFilterConfig(required=["schimmel"])
    item = _item(title="asfalt")
    assert pre_filter_item(item, cfg) is False
    assert item.metadata["passed_prefilter"] is False
    assert "requis" in item.metadata["prefilter_reason"]


# --- Filtrage d'une liste ----------------------------------------------------


def test_pre_filter_list_keeps_only_passing():
    cfg = PreFilterConfig(required=["schimmel"], excluded=["reclame"])
    items = [
        _item(title="schimmel in woning", url="https://e.org/1"),   # passe
        _item(title="asfalt wegenbouw", url="https://e.org/2"),     # rejete (pas requis)
        _item(title="schimmel reclame", url="https://e.org/3"),     # rejete (exclu)
    ]
    kept = pre_filter(items, cfg)
    assert len(kept) == 1
    assert str(kept[0].url) == "https://e.org/1"
    # Tous les items sont annotes, meme les rejetes.
    assert all("passed_prefilter" in i.metadata for i in items)
