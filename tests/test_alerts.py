"""Tests des alertes instantanees pour items critiques."""

from unittest.mock import MagicMock

from research_agent.delivery.alerts import (
    CRITICAL_SCORE,
    format_alert,
    is_critical,
    item_score,
    process_alerts,
    send_alert,
)
from research_agent.exceptions import DeliveryError
from research_agent.models import RawItem, Theme


def _item(score=95, theme=Theme.RESEARCH, title="Titre", justification=None, url="https://e.org/a"):
    meta = {"llm_score": score}
    if justification is not None:
        meta["llm_justification"] = justification
    return RawItem(source="test", title=title, url=url, theme=theme, metadata=meta)


# --- Detection du seuil critique ---------------------------------------------


def test_item_score_reads_metadata():
    assert item_score(_item(score=88)) == 88


def test_is_critical_threshold():
    assert is_critical(_item(score=90)) is True
    assert is_critical(_item(score=89)) is False


def test_custom_threshold():
    assert is_critical(_item(score=80), threshold=75) is True


# --- Format de l'alerte ------------------------------------------------------


def test_alert_includes_title_score_link():
    msg = format_alert(_item(score=93, title="Modele moisissure", url="https://openalex.org/W1"))
    assert "Modele moisissure" in msg
    assert "93" in msg
    assert "https://openalex.org/W1" in msg


def test_alert_priority_act_for_opportunity():
    msg = format_alert(_item(theme=Theme.OPPORTUNITY))
    assert "ACT" in msg


def test_alert_priority_high_for_others():
    msg = format_alert(_item(theme=Theme.RESEARCH))
    assert "HIGH" in msg


def test_alert_uses_justification_as_angle():
    msg = format_alert(_item(justification="correspond a notre produit"))
    assert "correspond a notre produit" in msg


# --- Envoi -------------------------------------------------------------------


def test_send_alert_success():
    client = MagicMock()
    client.send_message.return_value = 1
    assert send_alert(_item(), client=client) is True
    client.send_message.assert_called_once()


def test_send_alert_fallback_on_failure():
    client = MagicMock()
    client.send_message.side_effect = DeliveryError("boom")
    assert send_alert(_item(), client=client) is False  # non propage


# --- Traitement d'un lot -----------------------------------------------------


def test_process_alerts_only_critical_items():
    client = MagicMock()
    client.send_message.return_value = 1
    items = [
        _item(score=95, url="https://e.org/1"),  # critique
        _item(score=60, url="https://e.org/2"),  # non critique
        _item(score=91, url="https://e.org/3"),  # critique
    ]
    sent = process_alerts(items, client=client)
    assert sent == 2
    assert client.send_message.call_count == 2


def test_process_alerts_none_critical():
    client = MagicMock()
    items = [_item(score=50), _item(score=70, url="https://e.org/b")]
    assert process_alerts(items, client=client) == 0
    client.send_message.assert_not_called()


def test_process_alerts_empty():
    client = MagicMock()
    assert process_alerts([], client=client) == 0
    client.send_message.assert_not_called()
