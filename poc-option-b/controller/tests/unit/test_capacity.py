import pytest

from controller.app.capacity import CapacitySample, evaluate, parse_gpu_utilization, parse_memory

FREE = """               total        used        free      shared  buff/cache   available
Mem:          122000       12000       90000          10       20000      109981
Swap:              0           0           0
"""


def test_parse_memory_reads_available_column():
    assert parse_memory(FREE) == 109981


def test_parse_memory_rejects_unknown_output():
    with pytest.raises(ValueError):
        parse_memory("nothing here")


def test_parse_gpu_utilization():
    assert parse_gpu_utilization(" 7\n") == 7
    with pytest.raises(ValueError):
        parse_gpu_utilization("[N/A]")


def test_evaluate_reports_each_breach():
    assert evaluate(CapacitySample(100000, 0), min_available_mib=65536, max_gpu_util=20) == []
    reasons = evaluate(CapacitySample(1000, 90), min_available_mib=65536, max_gpu_util=20)
    assert len(reasons) == 2
