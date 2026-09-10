"""Tests de la configuration typee (Pydantic)."""

import pytest
from pydantic import ValidationError

from research_agent.config import AppConfig, Keywords, Secrets


def _valid_app_dict():
    return {
        "keywords": {"nl": ["schimmel"], "en": ["mould"], "de": ["schimmel"]},
        "relevance_threshold": 0.6,
        "sources": {
            "openalex": {"enabled": True, "query": "mould", "max_results": 20},
        },
        "delivery": {"channel": "telegram", "telegram": {"chat_id": "123"}},
    }


def test_appconfig_valid():
    app = AppConfig.model_validate(_valid_app_dict())
    assert app.relevance_threshold == 0.6
    assert app.sources["openalex"].max_results == 20
    assert app.delivery.channel == "telegram"


def test_keywords_all_lowercased_and_merged():
    kw = Keywords(nl=["Schimmel"], en=["Mould"], de=["Feuchtigkeit"])
    assert kw.all() == ["schimmel", "mould", "feuchtigkeit"]


def test_threshold_out_of_range_rejected():
    data = _valid_app_dict()
    data["relevance_threshold"] = 1.5
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_invalid_channel_rejected():
    data = _valid_app_dict()
    data["delivery"]["channel"] = "sms"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_email_channel_without_recipients_rejected():
    data = _valid_app_dict()
    data["delivery"] = {"channel": "email", "email": {"recipients": []}}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_secrets_require_raises_when_missing():
    secrets = Secrets(llm_api_key=None)
    with pytest.raises(ValueError):
        secrets.require("llm_api_key")
