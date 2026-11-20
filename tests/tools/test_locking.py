from gem_agent.tools.locking import acquire, locked, release


def test_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "run" / "outcomes.lock"

    with locked(path):
        assert acquire(path, blocking=False) is None

    handle = acquire(path, blocking=False)
    assert handle is not None
    release(handle)
    handle = acquire(path, blocking=False)
    assert handle is not None
    release(handle)
