"""Bounded, cancellable model calls and request-addressed genuine record/replay."""

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import ValidationError

from .errors import CopilotError, InvalidExtraction
from .grounding import validate_evidence
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT
from .schema import Intent, digest

LOCAL_MODEL = "mistral:latest"
LOCAL_DIGEST = "3944fe81ec14"  # Full digest is captured in artifacts/model-manifest.json.
HOSTED_MODEL = "gpt-4.1-mini-2025-04-14"


@dataclass(frozen=True)
class ModelConfig:
    mode: str = "replay"
    provider: str = "ollama"
    model: str = LOCAL_MODEL
    cassette_dir: Path = Path("evals/recordings")
    take: int = 0
    temperature: float = 0
    max_tokens: int = 400
    call_timeout: float = 25
    total_timeout: float = 52
    base_url: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    retries: int = 0
    provider_latency_ms: float = 0
    elapsed_ms: float = 0
    source: str = "none"
    request_hashes: list[str] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ModelResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    raw: dict


class Provider(Protocol):
    async def complete(self, request: dict, timeout: float) -> ModelResponse: ...


class HTTPProvider:
    def __init__(self, config: ModelConfig):
        self.config = config

    async def complete(self, request: dict, timeout: float) -> ModelResponse:
        config = self.config
        messages = request["messages"]
        headers: dict[str, str] = {}
        if config.provider == "ollama":
            url = (config.base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
            body = {"model": config.model, "messages": messages, "stream": False,
                    "format": request["schema"], "keep_alive": "30m",
                    "options": {"temperature": config.temperature, "num_predict": config.max_tokens,
                                "num_ctx": 4096, "seed": 42}}
        elif config.provider == "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise CopilotError("missing_api_key", "Live OpenAI mode requires OPENAI_API_KEY.")
            url = (config.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
            if not url.startswith("https://"):
                raise CopilotError("insecure_provider", "Hosted API credentials require HTTPS.")
            headers = {"Authorization": "Bearer " + key}
            body = {"model": config.model, "messages": messages,
                    "temperature": config.temperature, "max_completion_tokens": config.max_tokens,
                    "response_format": {"type": "json_schema", "json_schema": {
                        "name": "bulk_move_intent", "strict": True, "schema": request["schema"]}}}
        else:
            raise CopilotError("invalid_provider", "Choose ollama or openai.")
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            # wait_for in Extractor is the total wall deadline, including slow streaming bodies.
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
            if len(response.content) > 100_000:
                raise InvalidExtraction("Provider response exceeded the size limit")
            raw = response.json()
        elapsed = (time.perf_counter() - start) * 1000
        try:
            if config.provider == "ollama":
                if not raw.get("done") or raw.get("done_reason") == "length":
                    raise InvalidExtraction("Truncated model response")
                return ModelResponse(raw["message"]["content"], raw.get("prompt_eval_count", 0),
                                     raw.get("eval_count", 0), elapsed, raw)
            choice = raw["choices"][0]
            if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                raise InvalidExtraction("Provider refusal or truncated response")
            usage = raw.get("usage", {})
            return ModelResponse(choice["message"]["content"], usage.get("prompt_tokens", 0),
                                 usage.get("completion_tokens", 0), elapsed, raw)
        except (KeyError, IndexError, TypeError) as error:
            raise InvalidExtraction("Malformed provider envelope") from error


class Extractor:
    def __init__(self, config: ModelConfig, provider: Provider | None = None):
        if config.mode not in ("live", "record", "replay"):
            raise ValueError("mode must be live, record, or replay")
        self.config = config
        self.provider = provider or HTTPProvider(config)

    def request(self, instruction: str, repair: bool) -> dict:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction}]
        if repair:
            messages.append({"role": "user", "content":
                "Your previous output failed validation. Return the complete schema; copy only literal "
                "mentions, preserve every constraint and the from/to direction. Refuse if unsupported."})
        return {"prompt_version": PROMPT_VERSION, "provider": self.config.provider,
                "model": self.config.model, "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens, "schema": Intent.model_json_schema(),
                "messages": messages}

    async def _complete(self, request: dict, timeout: float) -> tuple[ModelResponse, str]:
        key = digest(request)
        path = self.config.cassette_dir / key / f"take-{self.config.take}.json"
        if self.config.mode == "replay":
            try:
                stored = json.loads(path.read_text())
            except FileNotFoundError as error:
                raise CopilotError("replay_miss", "No exact recording exists for this instruction and configuration. Use live mode.") from error
            if stored["request_hash"] != key or digest(stored["request"]) != key:
                raise CopilotError("recording_mismatch", "The recording request fingerprint does not match.")
            if digest(stored["response"]) != stored["response_hash"]:
                raise CopilotError("recording_mismatch", "The recording response checksum does not match.")
            return ModelResponse(**stored["response"]), key
        result = await self.provider.complete(request, timeout)
        if self.config.mode == "record":
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {"format_version": 1, "recorded_at": datetime.now(UTC).isoformat(),
                      "request_hash": key, "request": request, "response": asdict(result),
                      "response_hash": digest(asdict(result)), "provenance": "live-provider-response"}
            # Exclusive create keeps a subsequent run from silently replacing evidence.
            if path.exists():
                raise CopilotError("recording_exists", "This recording take already exists. Choose a new take or replay it.")
            with path.open("x") as stream:
                json.dump(record, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
        return result, key

    async def extract(self, instruction: str) -> tuple[Intent, Usage]:
        usage = Usage(source=self.config.mode)
        start = time.perf_counter()
        deadline = start + self.config.total_timeout
        last_code = "invalid_model_output"
        for attempt in range(2):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            timeout = min(self.config.call_timeout, remaining)
            request = self.request(instruction, repair=attempt > 0)
            usage.model_calls += 1
            usage.retries = attempt
            try:
                response, key = await asyncio.wait_for(self._complete(request, timeout), timeout)
                usage.request_hashes.append(key)
                usage.input_tokens += response.input_tokens
                usage.output_tokens += response.output_tokens
                usage.provider_latency_ms += response.latency_ms
                intent = Intent.model_validate_json(response.text)
                validate_evidence(intent, instruction)
                usage.elapsed_ms = (time.perf_counter() - start) * 1000
                return intent, usage
            except (TimeoutError, httpx.TimeoutException):
                last_code = "model_timeout"
            except httpx.HTTPStatusError as error:
                last_code = "model_unavailable"
                if error.response.status_code not in (408, 429, 500, 502, 503, 504):
                    break
            except httpx.TransportError:
                last_code = "model_unavailable"
            except (ValidationError, InvalidExtraction, json.JSONDecodeError, ValueError):
                last_code = "invalid_model_output"
            if attempt == 0:
                await asyncio.sleep(min(0.1, max(0, deadline - time.perf_counter())))
        usage.elapsed_ms = (time.perf_counter() - start) * 1000
        error = CopilotError(last_code, "Could not safely understand the instruction within two attempts. Nothing was moved; please retry or restate it.")
        error.usage = usage.as_dict()
        raise error
