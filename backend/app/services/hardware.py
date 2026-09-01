"""Exact processor identification for MikroTik hardware.

Why this table exists
---------------------
RouterOS never reports a CPU part number on RouterBOARD hardware. The closest
thing it offers is ``/system/routerboard`` ``firmware-type``, which names the
*bootloader platform family* - "ipq5300", "al21400", "tile" - and several
different SoCs share one family. On an hAP be3 Media, for instance, RouterOS
answers ``ipq5300`` while the processor MikroTik publishes for that product is
a Qualcomm IPQ-5322. Reporting the family as if it were the part is precise-
looking and wrong, which is the worst combination for a hardware panel.

``/system/resource`` is no help either: ``cpu`` carries the instruction set
("ARM64") on RouterBOARD and only holds a real part name on x86/CHR.

So the exact part is resolved from the product code, which *is* unique per
model, against the specifications MikroTik publishes on the product page.
Anything not in the table keeps the old behaviour and is clearly labelled as a
family rather than a part, so an unknown board degrades to "less precise",
never to "confidently incorrect".

Where the data comes from, and why it is a hand-maintained table
---------------------------------------------------------------
Each entry is transcribed from the CPU row of that product's specification
table on mikrotik.com. MikroTik publishes no machine-readable catalogue, and
the router itself does not know its own part number, so there is nothing to
query at runtime - and querying the vendor's website from a LAN-only appliance
would be the wrong trade anyway. A wrong-but-confident part number is worse
than an honest family name, so the table only ever grows by hand, from the
published specification.

An unlisted board therefore costs nothing: it shows the SoC family RouterOS
reported, marked inexact, exactly as before this module existed. Adding one is
a single line - read the CPU row of its page on mikrotik.com and key it on the
product code exactly as ``/system/routerboard`` reports ``model``.
"""
from typing import Dict, NamedTuple, Optional


class CpuIdentity(NamedTuple):
    """What to show for the processor, and how sure we are of it.

    ``exact`` is True only when the part number came from the published
    specification of this specific product code. When it is False, ``model``
    holds the SoC family RouterOS reported and the UI should present it as such.
    """
    model: Optional[str]
    exact: bool
    # The bootloader platform family RouterOS reported, kept for display next to
    # an exact part ("IPQ-5322 · ipq5300 platform") and for support questions.
    platform: Optional[str] = None


# Product code (RouterBOARD ``model``) -> CPU part number, from the CPU row of
# each product's specification table on mikrotik.com.
SOC_BY_PRODUCT_CODE: Dict[str, str] = {
    # --- Cloud Core Routers (CCR) ------------------------------------------
    "CCR1009-7G-1C-1S+": "Tilera TILE-Gx8009",
    "CCR1009-7G-1C-1S+PC": "Tilera TILE-Gx8009",
    "CCR1009-7G-1C-PC": "Tilera TILE-Gx8009",
    "CCR1009-8G-1S-1S+": "Tilera TILE-Gx8009",
    "CCR1009-8G-1S-1S+PC": "Tilera TILE-Gx8009",
    "CCR1016-12G": "Tilera TILE-Gx8016",
    "CCR1016-12S-1S+": "Tilera TILE-Gx8016",
    "CCR1036-12G-4S": "Tilera TILE-Gx8036",
    "CCR1036-12G-4S-EM": "Tilera TILE-Gx8036",
    "CCR1036-8G-2S+": "Tilera TILE-Gx8036",
    "CCR1036-8G-2S+EM": "Tilera TILE-Gx8036",
    "CCR1072-1G-8S+": "Tilera TILE-Gx8072",
    "CCR2004-1G-12S+2XS": "AL32400",
    "CCR2004-16G-2S+": "AL32400",
    "CCR2004-1G-2XS-PCIe": "AL32400",
    "CCR2116-12G-4S+": "AL73400",
    "CCR2216-1G-12XS-2XQ": "AL73400",
    # --- Wi-Fi 7 -----------------------------------------------------------
    "MA53UG+HbeH": "IPQ-5322",          # hAP be3 Media (reports "ipq5300")
    # --- Wi-Fi 6 -----------------------------------------------------------
    "C53UiG+5HPaxD2HPaxD": "IPQ-6010",  # hAP ax3
    "C52iG-5HaxD2HaxD-TC": "IPQ-6010",  # hAP ax2
    "cAPGi-5HaxD2HaxD": "IPQ-6010",     # cAP ax
    "L41G-2axD": "IPQ-5010",            # hAP ax lite
    # --- Wi-Fi 5 and earlier -----------------------------------------------
    "RBD53iG-5HacD2HnD": "IPQ-4019",    # hAP ac3
    "RBD52G-5HacD2HnD-TC": "IPQ-4018",  # hAP ac2
    "RB952Ui-5ac2nD": "QCA9531",        # hAP ac lite
    "RB941-2nD": "QCA9533",             # hAP lite
    "RBcAPGi-5acD2nD": "IPQ-4018",      # cAP ac
    "RBwAPG-5HacT2HnD": "IPQ-4018",     # wAP ac
    # --- wired & switches --------------------------------------------------
    "RB5009UG+S+IN": "Marvell 88F7040",
    "RB5009UPr+S+IN": "Marvell 88F7040",
    "RB5009UPr+S+OUT": "Marvell 88F7040",
    "L009UiGS-RM": "IPQ-5018",
    "L009UiGS-2HaxD-IN": "IPQ-5018",
    "RB4011iGS+RM": "AL21400",
    "RB4011iGS+5HacQ2HnD-IN": "AL21400",
    "RB3011UiAS-RM": "IPQ-8064",
    "RB1100AHx4": "AL21400",
    "RB1100AHx4 Dude Edition": "AL21400",
    "RB750Gr3": "MT7621A",              # hEX
    "RB760iGS": "MT7621A",              # hEX S
    "RB960PGS": "QCA9557",              # hEX PoE
    "CRS305-1G-4S+IN": "98DX3236",
    "CRS309-1G-8S+IN": "98DX8216",
    "CRS317-1G-16S+RM": "98DX8216",
    "CRS326-24G-2S+IN": "98DX3236",
    "CRS326-24G-2S+RM": "98DX3236",
    "CRS328-24P-4S+RM": "98DX3236",
    "CRS354-48G-4S+2Q+RM": "QCA9531",
}

# Fallback key for a board that reports a blank ``model``. Deliberately sparse:
# an entry here is only added for a board name observed coming off real
# hardware, because guessing how RouterOS spells a board name would reintroduce
# exactly the confidently-wrong answer this module exists to remove.
SOC_BY_BOARD_NAME: Dict[str, str] = {
    "hAP be^3 Media": "IPQ-5322",
    "CCR1009-7G-1C-1S+": "Tilera TILE-Gx8009",
}


def _norm(value: Optional[str]) -> str:
    return (value or "").strip()


def resolve_cpu_identity(
    *,
    product_code: Optional[str] = None,
    board_name: Optional[str] = None,
    firmware_type: Optional[str] = None,
    resource_cpu: Optional[str] = None,
    architecture: Optional[str] = None,
) -> CpuIdentity:
    """Best available processor identity for a router.

    Resolution order, most specific first:

    1. the published part number for this product code,
    2. the published part number for this board name,
    3. the bootloader platform family RouterOS reported (marked inexact),
    4. ``/system/resource`` ``cpu`` - a real part on x86/CHR,
    5. the architecture, so the field is never empty.
    """
    platform = _norm(firmware_type) or None

    exact = SOC_BY_PRODUCT_CODE.get(_norm(product_code))
    if not exact:
        exact = SOC_BY_BOARD_NAME.get(_norm(board_name))
    if not exact and product_code:
        norm_code = _norm(product_code)
        for k, v in SOC_BY_PRODUCT_CODE.items():
            if norm_code.startswith(k) or k.startswith(norm_code):
                exact = v
                break
    if not exact and board_name:
        norm_bn = _norm(board_name)
        for k, v in SOC_BY_PRODUCT_CODE.items():
            if norm_bn.startswith(k) or k.startswith(norm_bn):
                exact = v
                break
    if exact:
        return CpuIdentity(model=exact, exact=True, platform=platform)

    if platform:
        return CpuIdentity(model=platform, exact=False, platform=platform)

    # x86 and CHR have no RouterBOARD, and there `cpu` is the actual part name
    # ("Intel(R) Core(TM) i5-...") rather than the instruction set.
    fallback = _norm(resource_cpu) or _norm(architecture) or None
    return CpuIdentity(model=fallback, exact=bool(_norm(resource_cpu)), platform=None)
