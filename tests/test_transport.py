import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from conftest import FixedExtractor

from copilot.errors import CopilotError
from copilot.llm import HOSTED_MODEL, LOCAL_DIGEST, Extractor, ModelConfig
from copilot.schema import digest

TEXT = "Move open deals owned by Asha Verma from Qualified to Proposal Sent worth under INR 25000."
GOOD = FixedExtractor().intent.model_dump_json()


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs)
    )


def test_openai_strict_contract_and_credentials_never_recorded(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-contract-test-key")
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-contract-test-key"
        assert body["model"] == HOSTED_MODEL
        assert body["response_format"]["json_schema"]["strict"] is True
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        return httpx.Response(
            200,
            json={
                "model": HOSTED_MODEL,
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": GOOD}}
                ],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            },
        )

    mock_http(monkeypatch, handler)
    config = ModelConfig(
        mode="record", provider="openai", model=HOSTED_MODEL, cassette_dir=tmp_path
    )
    intent, usage = asyncio.run(Extractor(config).extract(TEXT))
    assert intent.owner == "Asha Verma" and usage.input_tokens == 123 and usage.output_tokens == 45
    assert len(requests) == 1
    assert "synthetic-contract-test-key" not in next(tmp_path.glob("*/*.json")).read_text()


@pytest.mark.parametrize("body", [[], {"choices": [None]}, {"choices": []}])
def test_malformed_provider_envelopes_fail_clearly(monkeypatch, body):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(CopilotError) as caught:
        asyncio.run(
            Extractor(ModelConfig(mode="live", provider="openai", model=HOSTED_MODEL)).extract(TEXT)
        )
    assert caught.value.code == "invalid_model_output"


@pytest.mark.parametrize("status,expected_calls", [(401, 1), (429, 2), (503, 2)])
def test_retry_policy_distinguishes_auth_errors_from_transient_errors(
    monkeypatch, status, expected_calls
):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "synthetic failure"}})

    mock_http(monkeypatch, handler)
    with pytest.raises(CopilotError) as caught:
        asyncio.run(
            Extractor(ModelConfig(mode="live", provider="openai", model=HOSTED_MODEL)).extract(TEXT)
        )
    assert caught.value.code == "model_unavailable" and len(calls) == expected_calls


def test_default_local_model_digest_is_verified(monkeypatch):
    requested = []

    def handler(request):
        requested.append(request.url.path)
        return httpx.Response(
            200, json={"models": [{"name": "mistral:latest", "digest": "different-weights"}]}
        )

    mock_http(monkeypatch, handler)
    with pytest.raises(CopilotError) as caught:
        asyncio.run(Extractor(ModelConfig(mode="live")).extract(TEXT))
    assert caught.value.code == "model_revision_mismatch" and requested == ["/api/tags"]


def test_ollama_schema_and_usage_contract(monkeypatch):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "mistral:latest", "digest": LOCAL_DIGEST}]}
            )
        body = json.loads(request.content)
        assert body["stream"] is False and body["format"]["additionalProperties"] is False
        assert body["options"]["num_predict"] == 400 and body["options"]["seed"] == 42
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": GOOD},
                "prompt_eval_count": 90,
                "eval_count": 20,
            },
        )

    mock_http(monkeypatch, handler)
    _, usage = asyncio.run(Extractor(ModelConfig(mode="live")).extract(TEXT))
    assert usage.input_tokens == 90 and usage.output_tokens == 20


def test_corrupt_recording_metadata_has_clear_failure(tmp_path):
    extractor = Extractor(ModelConfig(cassette_dir=tmp_path))
    key = digest(extractor.request(TEXT, False))
    directory = tmp_path / key
    directory.mkdir()
    (directory / "take-0.json").write_text("{}")
    with pytest.raises(CopilotError) as caught:
        asyncio.run(extractor.extract(TEXT))
    assert caught.value.code == "recording_corrupt"


def test_live_mode_never_replays_even_if_resume_was_set(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": GOOD}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    mock_http(monkeypatch, handler)
    config = ModelConfig(
        mode="record", provider="openai", model=HOSTED_MODEL, cassette_dir=tmp_path
    )
    asyncio.run(Extractor(config).extract(TEXT))
    _, usage = asyncio.run(Extractor(replace(config, mode="live", resume=True)).extract(TEXT))
    assert len(calls) == 2 and usage.replayed_calls == 0
