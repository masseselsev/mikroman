"""Exact processor identification.

RouterOS reports the SoC *family* on RouterBOARD hardware, never the part
number: an hAP be3 Media answers ``ipq5300`` while MikroTik publishes IPQ-5322
for that product. Showing the family as if it were the part is precise-looking
and wrong, so it is looked up from the product code and anything unknown is
flagged as inexact rather than guessed at.
"""
from backend.app.services.hardware import resolve_cpu_identity


def test_a_known_product_code_yields_the_published_part_number():
    cpu = resolve_cpu_identity(
        product_code="MA53UG+HbeH",
        board_name="hAP be^3 Media",
        firmware_type="ipq5300",
        resource_cpu="ARM64",
        architecture="arm64",
    )
    assert cpu.model == "IPQ-5322"
    assert cpu.exact is True
    # The family is kept for display and support questions, not discarded.
    assert cpu.platform == "ipq5300"


def test_the_board_name_resolves_it_when_the_product_code_is_blank():
    cpu = resolve_cpu_identity(
        product_code=None, board_name="hAP be^3 Media", firmware_type="ipq5300"
    )
    assert cpu == ("IPQ-5322", True, "ipq5300")


def test_an_unknown_board_falls_back_to_the_family_and_says_so():
    cpu = resolve_cpu_identity(
        product_code="RB-SOMETHING-NEW",
        board_name="hAP zz^9",
        firmware_type="ipq9999",
        resource_cpu="ARM64",
        architecture="arm64",
    )
    assert cpu.model == "ipq9999"
    assert cpu.exact is False


def test_x86_and_chr_report_a_real_part_in_resource_cpu():
    """No RouterBOARD, so `firmware_type` is empty and `cpu` is the real name."""
    cpu = resolve_cpu_identity(
        product_code=None,
        board_name="CHR",
        firmware_type=None,
        resource_cpu="Intel(R) Xeon(R) CPU E5-2670",
        architecture="x86_64",
    )
    assert cpu.model == "Intel(R) Xeon(R) CPU E5-2670"
    assert cpu.exact is True
    assert cpu.platform is None


def test_the_field_is_never_left_empty():
    cpu = resolve_cpu_identity(architecture="mipsbe")
    assert cpu.model == "mipsbe"
    assert cpu.exact is False


def test_whitespace_in_a_reported_product_code_does_not_break_the_lookup():
    assert resolve_cpu_identity(product_code="  MA53UG+HbeH ").model == "IPQ-5322"


def test_every_seeded_entry_names_a_part_and_not_a_family():
    """Guards the transcription: a family name here would defeat the module.

    RouterOS platform families are lowercase and unpunctuated ("ipq5300");
    published part numbers carry a vendor prefix, a dash or mixed case.
    """
    from backend.app.services.hardware import SOC_BY_BOARD_NAME, SOC_BY_PRODUCT_CODE

    for key, part in {**SOC_BY_PRODUCT_CODE, **SOC_BY_BOARD_NAME}.items():
        assert part == part.strip() and part, key
        assert part != part.lower(), f"{key} -> {part} looks like a platform family"
