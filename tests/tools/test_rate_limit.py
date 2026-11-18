import pytest

from gem_agent.tools.rate_limit import AdaptiveRateLimiter


def make(clock):
    return AdaptiveRateLimiter(floor=1.0, slow=4.0, recover_after=600, spike_median=2.0, clock=clock, sleep=clock.sleep)


async def test_first_request_does_not_wait(clock):
    limiter = make(clock)
    await limiter.wait()
    assert clock.sleeps == []


async def test_back_to_back_requests_are_spaced_by_the_floor(clock):
    limiter = make(clock)
    await limiter.wait()
    clock.now += 0.3
    await limiter.wait()
    assert clock.sleeps == pytest.approx([0.7])


async def test_real_error_slows_host_to_four_seconds(clock):
    limiter = make(clock)
    await limiter.wait()
    limiter.record(ok=False, latency=0.3)
    await limiter.wait()
    assert limiter.interval == 4.0
    assert clock.sleeps == [4.0]


async def test_steps_back_down_after_ten_clean_minutes(clock):
    limiter = make(clock)
    limiter.record(ok=False, latency=0.3)
    clock.now += 599
    limiter.record(ok=True, latency=0.3)
    assert limiter.interval == 4.0
    clock.now += 2
    limiter.record(ok=True, latency=0.3)
    assert limiter.interval == 1.0


async def test_latency_spike_counts_as_trouble(clock):
    limiter = make(clock)
    for _ in range(5):
        limiter.record(ok=True, latency=2.5)
    assert limiter.interval == 4.0


async def test_single_slow_response_is_not_a_spike(clock):
    limiter = make(clock)
    for latency in (0.3, 0.3, 3.0, 0.3, 0.3):
        limiter.record(ok=True, latency=latency)
    assert limiter.interval == 1.0


# --- shared across processes (one lock + state file per host) ------------------------------------------------


def shared(clock, path):
    return AdaptiveRateLimiter(floor=1.0, slow=4.0, recover_after=600, spike_median=2.0, clock=clock,
                               sleep=clock.sleep, shared_path=path)


async def test_limiters_sharing_a_file_space_requests_between_them(clock, tmp_path):
    a, b = shared(clock, tmp_path / "bidplus"), shared(clock, tmp_path / "bidplus")
    async with a.turn():
        pass
    clock.now += 0.25
    async with b.turn():
        pass
    assert clock.sleeps == pytest.approx([0.75])


async def test_error_seen_by_one_process_slows_the_other(clock, tmp_path):
    a, b = shared(clock, tmp_path / "bidplus"), shared(clock, tmp_path / "bidplus")
    async with a.turn():
        a.record(ok=False, latency=0.3)
    async with b.turn():
        pass
    assert b.interval == 4.0 and clock.sleeps == [4.0]


async def test_other_process_waits_while_a_request_is_in_flight(clock, tmp_path):
    import asyncio

    a, b = shared(clock, tmp_path / "bidplus"), shared(clock, tmp_path / "bidplus")
    order = []
    release = asyncio.Event()

    async def first():
        async with a.turn():
            order.append("a in")
            await release.wait()
            order.append("a out")

    async def second():
        async with b.turn():
            order.append("b in")

    task_a = asyncio.create_task(first())
    await asyncio.sleep(0.01)
    task_b = asyncio.create_task(second())
    await asyncio.sleep(0.1)
    assert order == ["a in"]
    release.set()
    await asyncio.gather(task_a, task_b)
    assert order == ["a in", "a out", "b in"]


async def test_hosts_do_not_share_a_file(clock, tmp_path):
    a, b = shared(clock, tmp_path / "bidplus"), shared(clock, tmp_path / "admin")
    async with a.turn():
        pass
    async with b.turn():
        pass
    assert clock.sleeps == []


async def test_unreadable_state_file_starts_fresh(clock, tmp_path):
    (tmp_path / "bidplus.json").write_text("not json")
    async with shared(clock, tmp_path / "bidplus").turn():
        pass
    assert clock.sleeps == []
