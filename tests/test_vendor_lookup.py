import httpx
import pytest
import respx

from backend.app.services.vendor_lookup import vendor_service


@pytest.mark.asyncio
async def test_builtin_oui_lookup():
    # Intel prefix
    assert vendor_service.lookup_sync("FC:6D:77:F8:5D:40") == "Intel"
    assert vendor_service.lookup_sync("fc-6d-77-00-11-22") == "Intel"

    # Apple prefix
    assert vendor_service.lookup_sync("AC:DE:48:12:34:56") == "Apple"

    # Raspberry Pi
    assert vendor_service.lookup_sync("B8:27:EB:AA:BB:CC") == "Raspberry Pi"

    # Espressif
    assert vendor_service.lookup_sync("48:A9:D2:11:22:33") == "Espressif (IoT)"

    # Randomized MACs
    assert vendor_service.lookup_sync("02:00:00:00:00:01") == "Private MAC (Randomized)"
    assert vendor_service.lookup_sync("D6:3D:1B:54:03:2F", hostname="iPhone") == "Apple (Private MAC)"
    assert vendor_service.lookup_sync("C6:DA:93:39:1E:C5", hostname="Pixel-9-Pro-XL") == "Google Pixel (Private MAC)"

    # Unknown non-randomized fallback
    assert vendor_service.lookup_sync("00:00:00:00:00:00") == "Unknown Vendor"


@pytest.mark.asyncio
async def test_online_oui_lookup_with_caching():
    mac = "00:11:22:33:44:55"
    with respx.mock() as respx_mock:
        respx_mock.get(f"https://api.maclookup.app/v2/macs/{mac}").mock(
            return_value=httpx.Response(200, json={"found": True, "company": "Cisco Systems, Inc"})
        )

        resolved = await vendor_service.lookup_async(mac)
        assert resolved == "Cisco"

        # Subsequent lookup should hit local cache without network call
        cached = vendor_service.lookup_sync(mac)
        assert cached == "Cisco"
