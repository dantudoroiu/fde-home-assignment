"""AnthropicClient behaviour with a fake SDK: validation retry, stop reasons, error mapping, usage/cost."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.llm.client import AnthropicClient, LLMError
from app.models import TriageResult
from tests.conftest import make_triage

VALID = make_triage().model_dump_json()


def _response(text=VALID, stop_reason="end_turn", model="claude-haiku-4-5"):
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        ),
    )


class FakeSDK:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(settings, *outcomes):
    records = []
    sdk = FakeSDK(*outcomes)
    client = AnthropicClient(settings, sink=records.append, sdk=sdk)
    return client, sdk, records


def _generate(client, model="claude-haiku-4-5"):
    return client.generate(
        step="triage", model=model, system="sys", user="<ticket>x</ticket>",
        schema=TriageResult, max_tokens=512, ticket_id=7,
    )


def test_valid_response_parsed_and_costed(settings):
    client, sdk, records = _client(settings, _response())
    result = _generate(client)

    assert result.category == "billing"
    assert len(records) == 1 and records[0].outcome == "ok"
    # Haiku 4.5: 1000 * $1/M + 200 * $5/M = $0.002
    assert records[0].cost_usd == pytest.approx(0.002)
    assert sdk.requests[0]["output_config"]["format"]["type"] == "json_schema"
    assert "fallbacks" not in sdk.requests[0]  # Haiku does not use server-side fallback


def test_dated_model_id_in_response_is_still_priced(settings):
    # Regression: the API echoes "claude-haiku-4-5-20251001", which used to price as $0.
    client, _, records = _client(settings, _response(model="claude-haiku-4-5-20251001"))
    _generate(client)
    assert records[0].cost_usd == pytest.approx(0.002)


def test_invalid_output_retried_once_then_succeeds(settings):
    bad = json.dumps({**json.loads(VALID), "confidence": 7})  # violates 0..1, which the API doesn't enforce
    client, sdk, records = _client(settings, _response(text=bad), _response())
    assert _generate(client).confidence == 0.9
    assert [r.outcome for r in records] == ["invalid_output", "ok"]
    assert [r.attempt for r in records] == [1, 2]


def test_invalid_output_twice_raises(settings):
    client, _, records = _client(settings, _response(text="not json"), _response(text="{}"))
    with pytest.raises(LLMError) as exc:
        _generate(client)
    assert exc.value.kind == "invalid_output"
    assert len(records) == 2


def test_refusal_is_not_parsed(settings):
    client, _, records = _client(settings, _response(text="", stop_reason="refusal"))
    with pytest.raises(LLMError) as exc:
        _generate(client)
    assert exc.value.kind == "refusal" and records[0].outcome == "refusal"


def test_truncated_output_is_not_parsed(settings):
    client, _, _ = _client(settings, _response(text='{"category": "bil', stop_reason="max_tokens"))
    with pytest.raises(LLMError) as exc:
        _generate(client)
    assert exc.value.kind == "truncated"


def _status_error(cls, code):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(code, request=request), body=None)


@pytest.mark.parametrize(
    "error, kind",
    [
        (lambda: _status_error(anthropic.RateLimitError, 429), "rate_limit"),
        (lambda: _status_error(anthropic.InternalServerError, 500), "api_error"),
        (lambda: _status_error(anthropic.AuthenticationError, 401), "config"),
        (lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")), "timeout"),
        (lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), "connection"),
    ],
)
def test_sdk_errors_mapped_to_llm_error_kinds(settings, error, kind):
    client, _, records = _client(settings, error())
    with pytest.raises(LLMError) as exc:
        _generate(client)
    assert exc.value.kind == kind
    assert records[0].outcome == kind


def test_concurrent_calls_are_capped(settings):
    # Regression: a burst of 40 seeded tickets fired 40 simultaneous calls and hit the org's
    # concurrency limit (429). Calls beyond llm_max_concurrency must wait instead.
    settings.llm_max_concurrency = 2
    in_flight = peak = 0
    lock = threading.Lock()

    class SlowSDK:
        def __init__(self):
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

        def _create(self, **request):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.05)
            with lock:
                in_flight -= 1
            return _response()

    client = AnthropicClient(settings, sink=lambda r: None, sdk=SlowSDK())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _generate(client), range(8)))

    assert len(results) == 8
    assert peak == 2


def _connection_error():
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))


def test_breaker_opens_after_repeated_outages_and_stops_calling_the_api(settings):
    settings.llm_breaker_failure_threshold = 3
    client, sdk, records = _client(settings, *[_connection_error() for _ in range(3)])
    for _ in range(3):
        with pytest.raises(LLMError):
            _generate(client)
    assert client.circuit_state == "open"

    with pytest.raises(LLMError) as exc:
        _generate(client)
    assert exc.value.kind == "circuit_open"
    assert len(sdk.requests) == 3  # the 4th call never reached the API
    assert records[-1].outcome == "circuit_open"


def test_calls_queued_during_a_burst_are_stopped_once_the_breaker_opens(settings):
    # Regression from a live outage simulation: 12 tickets arrived at once, passed the breaker
    # check while it was closed, queued for the slots, and all called the dead API anyway.
    settings.llm_max_concurrency = 1
    settings.llm_breaker_failure_threshold = 1
    api_calls = 0

    class DeadSDK:
        def __init__(self):
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

        def _create(self, **request):
            nonlocal api_calls
            api_calls += 1
            time.sleep(0.05)  # the others queue for the single slot meanwhile
            raise _connection_error()

    client = AnthropicClient(settings, sink=lambda r: None, sdk=DeadSDK())

    def call(_):
        try:
            _generate(client)
        except LLMError as exc:
            return exc.kind

    with ThreadPoolExecutor(max_workers=6) as pool:
        kinds = list(pool.map(call, range(6)))

    assert api_calls == 1
    assert sorted(kinds) == ["circuit_open"] * 5 + ["connection"]


def test_bad_answers_do_not_trip_the_breaker(settings):
    # Invalid output means the API is up and answering; only availability failures count.
    settings.llm_breaker_failure_threshold = 2
    client, _, _ = _client(settings, *[_response(text="not json") for _ in range(4)])
    for _ in range(2):
        with pytest.raises(LLMError) as exc:
            _generate(client)
        assert exc.value.kind == "invalid_output"
    assert client.circuit_state == "closed"


def test_sonnet_requests_use_refusal_fallback(settings):
    client, sdk, _ = _client(settings, _response(model="claude-sonnet-5-5"))
    _generate(client, model="claude-sonnet-5-5")
    assert sdk.requests[0]["fallbacks"] == "default"
    assert sdk.requests[0]["betas"] == ["server-side-fallback-2026-07-01"]
