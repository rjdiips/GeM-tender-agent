from gem_agent.retrieval.embed import pick_device


def test_forced_device_is_used_as_is():
    assert pick_device("cpu", free_gpu_mb=lambda: 20000) == "cpu"


def test_gpu_used_when_enough_memory_is_free():
    assert pick_device("auto", min_free_mb=3000, free_gpu_mb=lambda: 8000) == "cuda"


def test_cpu_when_vllm_holds_the_gpu():
    assert pick_device("auto", min_free_mb=3000, free_gpu_mb=lambda: 660) == "cpu"


def test_cpu_when_no_gpu_can_be_queried():
    assert pick_device("auto", free_gpu_mb=lambda: None) == "cpu"
