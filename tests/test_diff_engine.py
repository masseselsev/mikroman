from backend.app.services.diff_engine import DiffEngine

BASE_CONFIG = """/interface bridge
add name=bridge1
/ip address
add address=192.168.88.1/24 interface=bridge1
/ip pool
add name=pool1 ranges=192.168.88.10-192.168.88.100
"""

TARGET_CONFIG = """/interface bridge
add name=bridge1
/ip address
add address=192.168.88.1/24 interface=bridge1
add address=10.0.0.1/24 interface=ether2
/ip pool
add name=pool1 ranges=192.168.88.10-192.168.88.200
"""


def test_diff_engine_identical_texts():
    result = DiffEngine.diff_texts(BASE_CONFIG, BASE_CONFIG)
    assert result.lines_added == 0
    assert result.lines_removed == 0
    assert result.total_changes == 0
    assert len(result.hunks) == 0
    assert result.raw_unified == ""


def test_diff_engine_changes():
    result = DiffEngine.diff_texts(BASE_CONFIG, TARGET_CONFIG, fromfile="v1.rsc", tofile="v2.rsc")
    assert result.lines_added >= 2
    assert result.lines_removed >= 1
    assert result.total_changes == result.lines_added + result.lines_removed
    assert len(result.hunks) > 0
    assert "add address=10.0.0.1/24" in result.raw_unified

    # Check hunk structure
    hunk = result.hunks[0]
    assert hunk.old_start > 0
    assert hunk.new_start > 0
    types = [line.type for line in hunk.lines]
    assert "add" in types
    assert "del" in types
    assert "ctx" in types


def test_diff_engine_empty_inputs():
    result = DiffEngine.diff_texts("", "")
    assert result.total_changes == 0
    assert result.raw_unified == ""

    result_from_empty = DiffEngine.diff_texts("", "/ip address add\n")
    assert result_from_empty.lines_added == 1
    assert result_from_empty.lines_removed == 0
