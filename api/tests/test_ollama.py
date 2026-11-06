from __future__ import annotations

from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest

from app.config import Settings
from app.providers.llm import get_provider, ollama
from app.providers.llm.ollama import OllamaProvider
from app.schemas import Answer, Citation, Passage


def _settings() -> Settings:
    return Settings(
        llm_model="test-answer-model",
        ollama_url="http://ollama-test:11434/",
        temperature=0.1,
    )


def _passages() -> list[Passage]:
    return [
        Passage(
            chunk_id="rules-1",
            text="Students need at least 80% attendance.",
            source="academic-rules.pdf",
            section="1. Attendance",
            page=5,
            score=0.92,
            doc_version="2026",
        )
    ]


def _answer() -> Answer:
    return Answer(
        reasoning="The evidence states the requirement.",
        answer="Students need at least 80% attendance.",
        citations=[
            Citation(source="academic-rules.pdf", section="1. Attendance", page=5)
        ],
        answered_from_context=True,
        confidence="high",
    )


def _mock_post(monkeypatch: pytest.MonkeyPatch, *, content: str | None = None) -> Mock:
    response = Mock()
    response.is_error = False
    response.json.return_value = {
        "message": {"content": content if content is not None else _answer().model_dump_json()}
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(ollama.httpx, "post", post)
    return post


def test_generate_returns_validated_answer_and_sends_configured_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = _mock_post(monkeypatch)
    provider = OllamaProvider(_settings())

    answer = provider.generate("What attendance is required?", _passages())

    assert answer == _answer()
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args == ("http://ollama-test:11434/api/chat",)
    assert kwargs["timeout"] == 120.0
    payload = kwargs["json"]
    assert payload["model"] == "test-answer-model"
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0.1}
    assert payload["format"] == Answer.model_json_schema()
    assert payload["messages"][0]["role"] == "system"
    prompt = payload["messages"][1]["content"]
    assert prompt.index("Evidence passages") < prompt.index("Question:")
    assert "academic-rules.pdf" in prompt
    assert "Students need at least 80% attendance." in prompt
    assert "What attendance is required?" in prompt


def test_ollama_provider_is_selected_by_existing_factory() -> None:
    provider = get_provider(replace(_settings(), llm_provider="ollama-host"))

    assert isinstance(provider, OllamaProvider)


@pytest.mark.parametrize(
    "error_type",
    [httpx.ConnectError, httpx.TimeoutException],
)
def test_generate_reports_connection_and_timeout_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[httpx.RequestError],
) -> None:
    post = Mock(
        side_effect=error_type(
            "request failed",
            request=httpx.Request("POST", "http://ollama-test:11434/api/chat"),
        )
    )
    monkeypatch.setattr(ollama.httpx, "post", post)

    with pytest.raises(RuntimeError, match="Unable to connect to Ollama"):
        OllamaProvider(_settings()).generate("Question?", _passages())


def test_generate_reports_unsuccessful_http_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = _mock_post(monkeypatch)
    post.return_value.is_error = True
    post.return_value.status_code = 503
    post.return_value.text = "model unavailable"

    with pytest.raises(RuntimeError, match="HTTP 503: model unavailable"):
        OllamaProvider(_settings()).generate("Question?", _passages())


@pytest.mark.parametrize(
    "provider_response",
    [
        [],
        {},
        {"message": []},
        {"message": {"content": ""}},
        {"message": {"content": '{"answer":"missing required fields"}'}},
    ],
)
def test_generate_rejects_malformed_provider_responses(
    monkeypatch: pytest.MonkeyPatch,
    provider_response: object,
) -> None:
    post = _mock_post(monkeypatch)
    post.return_value.json.return_value = provider_response

    with pytest.raises(RuntimeError, match="Ollama"):
        OllamaProvider(_settings()).generate("Question?", _passages())


def test_generate_reports_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    post = _mock_post(monkeypatch)
    post.return_value.json.side_effect = ValueError("invalid JSON")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        OllamaProvider(_settings()).generate("Question?", _passages())


def test_generate_rejects_temperature_above_assignment_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = _mock_post(monkeypatch)
    provider = OllamaProvider(replace(_settings(), temperature=0.31))

    with pytest.raises(ValueError, match="temperature must be between 0.0 and 0.3"):
        provider.generate("Question?", _passages())
    post.assert_not_called()
