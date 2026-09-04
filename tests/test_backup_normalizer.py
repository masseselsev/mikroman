from backend.app.services.backup_normalizer import compute_fingerprint, normalize_rsc

SAMPLE_RSC_1 = """# 2026-09-04 15:21:49 by RouterOS 7.15.2
# software id = ABCD-1234
#
# model = RB5009UG+S+IN
# serial number = 1234567890AB
/ip pool add name=dhcp ranges=192.168.88.10-192.168.88.254
/ip dhcp-server add address-pool=dhcp disabled=no interface=bridge name=defconf
"""

SAMPLE_RSC_2 = """# 2026-09-05 03:00:00 by RouterOS 7.15.2
# software id = ABCD-1234
#
# model = RB5009UG+S+IN
# serial number = 1234567890AB
/ip pool add name=dhcp ranges=192.168.88.10-192.168.88.254
/ip dhcp-server add address-pool=dhcp disabled=no interface=bridge name=defconf
"""

SAMPLE_RSC_CHANGED = """# 2026-09-05 03:00:00 by RouterOS 7.15.2
# software id = ABCD-1234
#
# model = RB5009UG+S+IN
/ip pool add name=dhcp ranges=192.168.88.10-192.168.88.200
/ip dhcp-server add address-pool=dhcp disabled=no interface=bridge name=defconf
"""


def test_normalize_rsc_strips_volatile_timestamp():
    norm1 = normalize_rsc(SAMPLE_RSC_1)
    norm2 = normalize_rsc(SAMPLE_RSC_2)
    assert norm1 == norm2
    assert "2026-09-04 15:21:49" not in norm1
    assert "2026-09-05 03:00:00" not in norm2
    assert "/ip pool add name=dhcp" in norm1


def test_compute_fingerprint():
    fp1 = compute_fingerprint(SAMPLE_RSC_1)
    fp2 = compute_fingerprint(SAMPLE_RSC_2)
    fp_changed = compute_fingerprint(SAMPLE_RSC_CHANGED)

    assert fp1 == fp2
    assert len(fp1) == 64
    assert fp1 != fp_changed


def test_normalize_empty_or_whitespace():
    assert normalize_rsc("") == ""
    assert normalize_rsc("   \n\r\n  ") == ""
    assert len(compute_fingerprint("")) == 64
