"""Bounded, cancellable model calls and request-addressed genuine record/replay."""

import asyncio
import json
import os
import tempfile
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
LOCAL_DIGEST = "3944fe81ec14610e0852c3d915768ee8d507ea541387fdfcbbf9edaa0c757734"
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
    resume: bool = False


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
    replayed_calls: int = 0
    tokens_unknown_calls: int = 0

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


def parse_intent(text: str) -> Intent:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidExtraction("Duplicate JSON keys are forbidden")
            result[key] = value
        return result

    return Intent.model_validate(json.loads(text, object_pairs_hook=unique_keys))


def publish_recording(path: Path, record: dict):
    """Publish a complete cassette atomically; concurrent writers cannot overwrite it."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".recording-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Unlike replace(), link() fails if another writer already published this take.
        os.link(temporary, path)
    except FileExistsError as error:
        raise CopilotError(
            "recording_exists", "This take already exists. Choose a new take or replay it."
        ) from error
    except OSError as error:
        raise CopilotError(
            "recording_write_failed", "Could not persist a complete recording; no plan was issued."
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class HTTPProvider:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.verified = False

    async def complete(self, request: dict, timeout: float) -> ModelResponse:
        config = self.config
        messages = request["messages"]
        headers: dict[str, str] = {}
        if config.provider == "ollama":
            url = (config.base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
            body = {
                "model": config.model,
                "messages": messages,
                "stream": False,
                "format": request["schema"],
                "keep_alive": "30m",
                "options": {
                    "temperature": config.temperature,
                    "num_predict": config.max_tokens,
                    "num_ctx": 4096,
                    "seed": 42,
                },
            }
        elif config.provider == "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise CopilotError("missing_api_key", "Live OpenAI mode requires OPENAI_API_KEY.")
            url = (config.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
            if not url.startswith("https://"):
                raise CopilotError("insecure_provider", "Hosted API credentials require HTTPS.")
            headers = {"Authorization": "Bearer " + key}
            body = {
                "model": config.model,
                "messages": messages,
                "temperature": config.temperature,
                "max_completion_tokens": config.max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "bulk_move_intent",
                        "strict": True,
                        "schema": request["schema"],
                    },
                },
            }
        else:
            raise CopilotError("invalid_provider", "Choose ollama or openai.")
        start = time.perf_counter()
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False
        ) as client:
            if config.provider == "ollama" and config.model == LOCAL_MODEL and not self.verified:
                tags = await client.get(
                    (config.base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
                )
                tags.raise_for_status()
                listing = tags.json()
                if not isinstance(listing, dict) or not isinstance(listing.get("models"), list):
                    raise InvalidExtraction("Malformed provider model listing")
                matching = [
                    m
                    for m in listing["models"]
                    if isinstance(m, dict) and m.get("name") == LOCAL_MODEL
                ]
                if len(matching) != 1 or matching[0].get("digest") != LOCAL_DIGEST:
                    raise CopilotError(
                        "model_revision_mismatch",
                        "The installed Mistral model differs from the recorded revision. Select an explicit model override and create a new evaluation.",
                    )
                self.verified = True
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
                return ModelResponse(
                    raw["message"]["content"],
                    raw.get("prompt_eval_count", 0),
                    raw.get("eval_count", 0),
                    elapsed,
                    raw,
                )
            choice = raw["choices"][0]
            if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                raise InvalidExtraction("Provider refusal or truncated response")
            usage = raw.get("usage", {})
            return ModelResponse(
                choice["message"]["content"],
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                elapsed,
                raw,
            )
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise InvalidExtraction("Malformed provider envelope") from error


class Extractor:
    def __init__(self, config: ModelConfig, provider: Provider | None = None):
        if config.mode not in ("live", "record", "replay"):
            raise ValueError("mode must be live, record, or replay")
        self.config = config
        self.provider = provider or HTTPProvider(config)

    def request(self, instruction: str, repair: bool, hint: str = "") -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ]
        if repair:
            messages.append(
                {
                    "role": "user",
                    "content": "Your previous output failed validation. Return the complete schema; copy only literal "
                    "mentions, preserve every constraint and the from/to direction. Refuse if unsupported. "
                    "Validator feedback: " + hint,
                }
            )
        return {
            "prompt_version": PROMPT_VERSION,
            "provider": self.config.provider,
            "model": self.config.model,
            "temperature": self.config.temperature,
            "model_revision": LOCAL_DIGEST
            if self.config.provider == "ollama" and self.config.model == LOCAL_MODEL
            else self.config.model,
            "max_tokens": self.config.max_tokens,
            "schema": Intent.model_json_schema(),
            "messages": messages,
        }

    async def _complete(self, request: dict, timeout: float) -> tuple[ModelResponse, str, bool]:
        key = digest(request)
        path = self.config.cassette_dir / key / f"take-{self.config.take}.json"
        if self.config.mode == "replay" or (
            self.config.mode == "record" and self.config.resume and path.exists()
        ):
            try:
                stored = json.loads(path.read_text())
            except FileNotFoundError as error:
                raise CopilotError(
                    "replay_miss",
                    "No exact recording exists for this instruction and configuration. Use live mode.",
                ) from error
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise CopilotError(
                    "recording_corrupt", "The recording cannot be read as complete JSON."
                ) from error
            try:
                if stored["request_hash"] != key or digest(stored["request"]) != key:
                    raise CopilotError(
                        "recording_mismatch", "The recording request fingerprint does not match."
                    )
                if digest(stored["response"]) != stored["response_hash"]:
                    raise CopilotError(
                        "recording_mismatch", "The recording response checksum does not match."
                    )
                return ModelResponse(**stored["response"]), key, True
            except (KeyError, TypeError) as error:
                raise CopilotError(
                    "recording_corrupt", "Recording metadata is incomplete or malformed."
                ) from error
        if self.config.mode == "record" and path.exists():
            # Refuse before spending a provider call. Publication also guards the race.
            raise CopilotError(
                "recording_exists", "This take already exists. Choose a new take or replay it."
            )
        result = await self.provider.complete(request, timeout)
        if self.config.mode == "record":
            record = {
                "format_version": 1,
                "recorded_at": datetime.now(UTC).isoformat(),
                "request_hash": key,
                "request": request,
                "response": asdict(result),
                "response_hash": digest(asdict(result)),
                "provenance": "live-provider-response",
            }
            publish_recording(path, record)
        return result, key, False

    async def extract(self, instruction: str) -> tuple[Intent, Usage]:
        usage = Usage(source=self.config.mode)
        start = time.perf_counter()
        deadline = start + self.config.total_timeout
        last_code = "invalid_model_output"
        repair_hint = ""
        for attempt in range(2):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            timeout = min(self.config.call_timeout, remaining)
            request = self.request(instruction, repair=bool(repair_hint), hint=repair_hint)
            usage.model_calls += 1
            usage.retries = attempt
            try:
                response, key, replayed = await asyncio.wait_for(
                    self._complete(request, timeout), timeout
                )
                usage.replayed_calls += int(replayed)
                usage.request_hashes.append(key)
                usage.input_tokens += response.input_tokens
                usage.output_tokens += response.output_tokens
                usage.provider_latency_ms += response.latency_ms
                intent = parse_intent(response.text)
                validate_evidence(intent, instruction)
                usage.elapsed_ms = (time.perf_counter() - start) * 1000
                return intent, usage
            except (TimeoutError, httpx.TimeoutException):
                last_code = "model_timeout"
                usage.tokens_unknown_calls += 1
            except httpx.HTTPStatusError as error:
                last_code = "model_unavailable"
                if error.response.status_code not in (408, 429, 500, 502, 503, 504):
                    break
            except httpx.TransportError:
                last_code = "model_unavailable"
                usage.tokens_unknown_calls += 1
            except InvalidExtraction as error:
                # These messages are fixed application strings, not model/provider content.
                last_code = "invalid_model_output"
                repair_hint = str(error)
            except (ValidationError, json.JSONDecodeError, ValueError, TypeError):
                last_code = "invalid_model_output"
                repair_hint = "Return all required keys, exactly typed, with JSON null for absent fields and no extra keys."
            if attempt == 0:
                await asyncio.sleep(min(0.1, max(0, deadline - time.perf_counter())))
        usage.elapsed_ms = (time.perf_counter() - start) * 1000
        error = CopilotError(
            last_code,
            "Could not safely understand the instruction within two attempts. Nothing was moved; please retry or restate it.",
        )
        error.usage = usage.as_dict()
        raise error
