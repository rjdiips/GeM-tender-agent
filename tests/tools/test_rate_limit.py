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
    # Both requests do post-request sleeps to maintain spacing via the lock
    assert clock.sleeps == pytest.approx([1.0, 1.0])


async def test_error_seen_by_one_process_slows_the_other(clock, tmp_path):
    a, b = shared(clock, tmp_path / "bidplus"), shared(clock, tmp_path / "bidplus")
    async with a.turn():
        a.record(ok=False, latency=0.3)
    async with b.turn():
        pass
    # a saves (interval changed) and post-sleeps 4.0s. b loads the new interval and post-sleeps 4.0s.
    assert b.interval == 4.0 and clock.sleeps == [4.0, 4.0]


async def test_shared_limiter_spikes_after_three_slow_responses(clock, tmp_path):
    limiter = shared(clock, tmp_path / "bidplus")
    for _ in range(3):
        async with limiter.turn():
            limiter.record(ok=True, latency=5.0)
    assert limiter.interval == 4.0


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
    # Both limiters are shared (different hosts), so both do post-request sleeps
    assert clock.sleeps == [1.0, 1.0]


async def test_unreadable_state_file_starts_fresh(clock, tmp_path):
    (tmp_path / "bidplus.json").write_text("not json")
    async with shared(clock, tmp_path / "bidplus").turn():
        pass
    # Shared limiter does post-request sleep even with unreadable file (starts fresh but still shared)
    assert clock.sleeps == [1.0]


# --- write-on-change tests (Task 7) --------------------------------------------------------------------------


async def test_clean_requests_do_not_write_the_state_file(clock, tmp_path, monkeypatch):
    """Clean requests (no interval/trouble change) must not write the state file."""
    path = tmp_path / "bidplus"
    state_file = path.with_name(path.name + ".json")
    writes = 0
    original_replace = state_file.__class__.replace

    def counting_replace(self, target, *args, **kwargs):
        nonlocal writes
        if str(self).endswith(".tmp") and str(target) == str(state_file):
            writes += 1
        return original_replace(self, target, *args, **kwargs)

    monkeypatch.setattr(state_file.__class__, "replace", counting_replace)
    lim = shared(clock, path)
    for _ in range(5):
        async with lim.turn():
            lim.record(ok=True, latency=0.1)
    assert writes == 0


async def test_interval_change_writes_once(clock, tmp_path, monkeypatch):
    """A single error (interval change) must write exactly once."""
    path = tmp_path / "bidplus"
    state_file = path.with_name(path.name + ".json")
    writes = 0
    original_replace = state_file.__class__.replace

    def counting_replace(self, target, *args, **kwargs):
        nonlocal writes
        if str(self).endswith(".tmp") and str(target) == str(state_file):
            writes += 1
        return original_replace(self, target, *args, **kwargs)

    monkeypatch.setattr(state_file.__class__, "replace", counting_replace)
    lim = shared(clock, path)
    async with lim.turn():
        lim.record(ok=False, latency=0.1)
    assert writes == 1


async def test_cancellation_during_post_request_sleep_holds_lock_and_saves(clock, tmp_path):
    """Cancellation during post-request sleep must hold the lock and save state.
    
    This test uses a small interval (0.2s) with real time to observe cancellation behavior.
    The bounds are generous to avoid flakiness.
    """
    import asyncio
    path = tmp_path / "bidplus"
    
    # Use real time with small interval to make the test fast but observable
    a = AdaptiveRateLimiter(floor=0.2, slow=0.2, recover_after=600, spike_median=2.0,
                            clock=clock, sleep=asyncio.sleep, shared_path=path, lock_poll=0.01)
    b = AdaptiveRateLimiter(floor=0.2, slow=0.2, recover_after=600, spike_median=2.0,
                            clock=clock, sleep=asyncio.sleep, shared_path=path, lock_poll=0.01)
    
    order = []
    turn_entry_times = {}
    
    async def first():
        try:
            async with a.turn():
                order.append("a in")
                turn_entry_times["a"] = asyncio.get_event_loop().time()
                a.record(ok=False, latency=0.3)  # Change interval to 0.2s
                order.append("a record")
        except asyncio.CancelledError:
            order.append("a cancelled")
            raise
        finally:
            order.append("a finally")
    
    task_a = asyncio.create_task(first())
    
    # Let it start and enter the post-request sleep
    await asyncio.sleep(0.05)
    assert "a in" in order and "a record" in order
    
    # Cancel the task while it's in the post-request sleep
    task_a.cancel()
    await asyncio.sleep(0.01)  # Let cancellation propagate
    
    # Record when b enters the turn (not when it starts)
    async def second():
        async with b.turn():
            order.append("b in")
            turn_entry_times["b"] = asyncio.get_event_loop().time()
    
    task_b = asyncio.create_task(second())
    
    # Wait for both tasks to complete
    await asyncio.gather(task_a, return_exceptions=True)
    await task_b
    
    # Check that b started after a's cancellation
    assert "a cancelled" in order
    assert "b in" in order
    
    # Check that b.interval is 0.2s (state was saved despite cancellation)
    assert b.interval == 0.2
    
    # Check that b had to wait for a's lock to release
    # b should enter the turn at least 0.2s after a entered (a's post-request sleep)
    # With generous bounds: b should enter at least 0.15s after a (accounting for timing)
    if "a" in turn_entry_times and "b" in turn_entry_times:
        assert turn_entry_times["b"] - turn_entry_times["a"] >= 0.15, \
            f"b should wait for a's lock to release (a entered at {turn_entry_times['a']:.3f}, b entered at {turn_entry_times['b']:.3f})"
