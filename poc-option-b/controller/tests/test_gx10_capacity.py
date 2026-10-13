from scripts.check_gx10_capacity import evaluate, parse_containers, parse_gpu_utilization, parse_memory


def test_capacity_parsers_and_reject_busy_server():
    memory = "               total used free shared buff/cache available\nMem: 124610 38006 46924 1124 41898 86603\n"
    assert parse_memory(memory) == 86603
    assert parse_gpu_utilization("NVIDIA GB10, 41\n") == 41
    containers = parse_containers("app\tUp 3 days (healthy)\ncdp\tUp 3 weeks (unhealthy)\n")
    sample = {"available_mib": 86603, "gpu_util_percent": 41, "containers": containers}
    reasons = evaluate([sample, sample], min_available_mib=65536, max_gpu_util=20)
    assert any("GPU utilization" in reason for reason in reasons)
    assert any("unhealthy" in reason for reason in reasons)


def test_capacity_rejects_container_change():
    first = {"available_mib": 90000, "gpu_util_percent": 5, "containers": {"app": "Up"}}
    second = {"available_mib": 90000, "gpu_util_percent": 5, "containers": {}}
    assert any("container set changed" in reason for reason in evaluate(
        [first, second], min_available_mib=65536, max_gpu_util=20,
    ))


def test_capacity_allows_only_named_preexisting_unhealthy_apps():
    first = {"available_mib": 100000, "gpu_util_percent": 0,
             "containers": {"cdp": "Up (unhealthy)", "app": "Up (healthy)"}}
    assert evaluate(
        [first, first], min_available_mib=65536, max_gpu_util=20,
        allowed_unhealthy=frozenset({"cdp"}),
    ) == []
    second = {**first, "containers": {"cdp": "Up (unhealthy)", "app": "Up (unhealthy)"}}
    assert any("app" in reason for reason in evaluate(
        [first, second], min_available_mib=65536, max_gpu_util=20,
        allowed_unhealthy=frozenset({"cdp", "app"}),
    ))
