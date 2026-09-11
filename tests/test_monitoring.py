"""Tests du monitoring leger (logs fichier + alerte Telegram sur crash)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from research_agent import monitoring
from research_agent.exceptions import DeliveryError
from research_agent.monitoring import (
    guard,
    notify_failure,
    setup_monitoring,
    _truncate_trace,
)


# --- Journalisation fichier --------------------------------------------------


def test_setup_monitoring_creates_log_file(tmp_path):
    log_file = tmp_path / "logs" / "agent.log"
    setup_monitoring(log_file=log_file)
    logger = logging.getLogger("research_agent")
    logger.info("evenement de test")
    # Un handler fichier doit exister et le dossier etre cree.
    assert log_file.parent.is_dir()
    from logging.handlers import RotatingFileHandler

    assert any(isinstance(h, RotatingFileHandler) for h in logger.handlers)


# --- Troncature de la stacktrace ---------------------------------------------


def test_truncate_trace_limits_length():
    try:
        raise ValueError("erreur de test")
    except ValueError as exc:
        trace = _truncate_trace(exc, limit=50)
    assert len(trace) <= 50
    assert "ValueError" in trace or "erreur" in trace


def test_truncate_trace_short_unchanged():
    try:
        raise RuntimeError("court")
    except RuntimeError as exc:
        trace = _truncate_trace(exc, limit=5000)
    assert "RuntimeError" in trace


# --- Alerte d'echec ----------------------------------------------------------


def test_notify_failure_sends_alert():
    client = MagicMock()
    client.send_message.return_value = 1
    exc = ValueError("boom")
    assert notify_failure("daily", exc, run_id="42", client=client) is True
    sent = client.send_message.call_args[0][0]
    assert "ECHEC CRITIQUE" in sent
    assert "daily" in sent
    assert "42" in sent
    assert "boom" in sent


def test_notify_failure_returns_false_on_delivery_error():
    client = MagicMock()
    client.send_message.side_effect = DeliveryError("telegram down")
    assert notify_failure("weekly", RuntimeError("x"), client=client) is False


# --- Guard : interception + alerte + relance ---------------------------------


def test_guard_catches_alerts_and_reraises():
    client = MagicMock()
    client.send_message.return_value = 1
    with pytest.raises(RuntimeError):
        with guard("daily", run_id="7", client=client):
            raise RuntimeError("crash pipeline")
    # L'alerte a bien ete declenchee avec les infos du run.
    client.send_message.assert_called_once()
    sent = client.send_message.call_args[0][0]
    assert "daily" in sent and "crash pipeline" in sent


def test_guard_passes_through_on_success():
    client = MagicMock()
    with guard("weekly", client=client):
        pass  # aucune exception
    client.send_message.assert_not_called()  # pas d'alerte si tout va bien


# --- Integration avec la CLI -------------------------------------------------


def test_cli_triggers_alert_on_crash():
    from research_agent import main as cli

    with patch("research_agent.main.setup_monitoring"), \
         patch("research_agent.main.run_daily", side_effect=RuntimeError("fatal")), \
         patch("research_agent.monitoring.TelegramClient") as tg_cls:
        tg = MagicMock()
        tg.send_message.return_value = 1
        tg_cls.return_value = tg
        with pytest.raises(RuntimeError):
            cli.main(["daily"])
    # Le crash a bien declenche une alerte Telegram.
    tg.send_message.assert_called_once()
    assert "ECHEC CRITIQUE" in tg.send_message.call_args[0][0]
