import pytest
import respx

from backend.app.services.changelog import ChangelogService, validate_version


def test_validate_version():
    assert validate_version("7.16") == "7.16"
    assert validate_version("7.15.2") == "7.15.2"
    with pytest.raises(ValueError):
        validate_version("../../etc/passwd")
    with pytest.raises(ValueError):
        validate_version("7.16; rm -rf")
    with pytest.raises(ValueError):
        validate_version("")


@pytest.mark.asyncio
async def test_changelog_service_caching_and_limits():
    service = ChangelogService()
    sample_notes = "*) bridge - fixed vlan filtering;\n*) wifi - improved mlo stability;\n"

    with respx.mock(assert_all_called=False) as respx_mock:
        route = respx_mock.get("https://upgrade.mikrotik.com/routeros/7.16.1/CHANGELOG").respond(
            status_code=200, text=sample_notes
        )

        notes1 = await service.get_notes("7.16.1")
        assert notes1 == sample_notes.strip()
        assert route.call_count == 1

        # Second call must hit memory cache, not HTTP
        notes2 = await service.get_notes("7.16.1")
        assert notes2 == sample_notes.strip()
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_changelog_service_negative_ttl_and_404():
    service = ChangelogService()

    with respx.mock(assert_all_called=False) as respx_mock:
        route = respx_mock.get("https://upgrade.mikrotik.com/routeros/99.99/CHANGELOG").respond(
            status_code=404
        )

        with pytest.raises(RuntimeError):
            await service.get_notes("99.99")
        assert route.call_count == 1

        # Negative cache hit within TTL
        with pytest.raises(RuntimeError):
            await service.get_notes("99.99")
        assert route.call_count == 1

