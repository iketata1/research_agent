"""Tests du logging centralise et des exceptions personnalisees."""

import logging

from research_agent.exceptions import (
    ConnectorError,
    DatabaseError,
    LLMError,
    ResearchAgentBaseException,
    ResearchAgentError,
)
from research_agent.logging_config import ROOT_LOGGER_NAME, get_logger, setup_logging


# --- Exceptions --------------------------------------------------------------


def test_exception_hierarchy():
    assert issubclass(ConnectorError, ResearchAgentError)
    assert issubclass(LLMError, ResearchAgentError)
    assert issubclass(DatabaseError, ResearchAgentError)


def test_base_alias_is_root():
    assert ResearchAgentBaseException is ResearchAgentError


def test_connector_error_carries_source():
    err = ConnectorError("timeout", source="aedes")
    assert err.source == "aedes"
    assert "aedes" in str(err)


def test_llm_error_carries_provider():
    err = LLMError("rate limited", provider="openai")
    assert err.provider == "openai"
    assert "openai" in str(err)


def test_catch_all_via_base():
    """Une erreur specifique doit etre attrapable par la classe de base."""
    try:
        raise ConnectorError("boom", source="ted")
    except ResearchAgentError as caught:
        assert isinstance(caught, ConnectorError)


# --- Logging -----------------------------------------------------------------


def test_get_logger_is_namespaced():
    logger = get_logger("collectors.openalex")
    assert logger.name == f"{ROOT_LOGGER_NAME}.collectors.openalex"


def test_setup_logging_is_idempotent():
    first = setup_logging()
    handler_count = len(first.handlers)
    second = setup_logging()
    # Pas de handlers dupliques a la deuxieme configuration.
    assert len(second.handlers) == handler_count
    assert first is second


def test_setup_logging_level_applies():
    logger = setup_logging(level=logging.DEBUG)
    assert logger.level == logging.DEBUG


def test_file_handler_writes(tmp_path):
    log_file = tmp_path / "agent.log"
    # Nouveau logger isole pour ne pas dependre de l'etat global.
    logger = logging.getLogger("research_agent.test_file")
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(log_file, encoding="utf-8")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("ligne de test")
    handler.flush()
    assert log_file.exists()
    assert "ligne de test" in log_file.read_text(encoding="utf-8")
