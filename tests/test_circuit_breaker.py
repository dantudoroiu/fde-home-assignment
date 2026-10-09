from app.llm.circuit_breaker import CircuitBreaker


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _breaker(threshold=3, cooldown=60):
    clock = FakeClock()
    return CircuitBreaker(threshold, cooldown, clock=clock), clock


def test_opens_after_threshold_consecutive_failures():
    breaker, _ = _breaker(threshold=3)
    for _ in range(2):
        assert breaker.allow_request()
        breaker.record_failure()
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open"
    assert not breaker.allow_request()


def test_success_resets_the_failure_count():
    breaker, _ = _breaker(threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"  # never 3 in a row


def test_after_cooldown_exactly_one_trial_call_goes_through():
    breaker, clock = _breaker(threshold=1, cooldown=60)
    breaker.record_failure()
    clock.now = 59
    assert not breaker.allow_request()
    clock.now = 60
    assert breaker.allow_request()  # the probe
    assert breaker.state == "half_open"
    assert not breaker.allow_request()  # everyone else still fails fast


def test_successful_trial_closes_the_breaker():
    breaker, clock = _breaker(threshold=1, cooldown=60)
    breaker.record_failure()
    clock.now = 60
    assert breaker.allow_request()
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow_request()


def test_failed_trial_reopens_for_a_fresh_cooldown():
    breaker, clock = _breaker(threshold=1, cooldown=60)
    breaker.record_failure()
    clock.now = 60
    assert breaker.allow_request()
    breaker.record_failure()
    assert breaker.state == "open"
    clock.now = 119
    assert not breaker.allow_request()  # cooldown restarted at t=60
    clock.now = 120
    assert breaker.allow_request()
