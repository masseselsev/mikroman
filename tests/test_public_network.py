"""Public uplink identity: org-string parsing, caching and failure behaviour.

None of these tests reach the internet - the fetch is stubbed - because the
point under test is the resolver's contract, not a third-party service's
availability.
"""

import pytest

from backend.app.services.public_network import (
    _MAX_NAME_LENGTH,
    FAILURE_TTL_SECONDS,
    SUCCESS_TTL_SECONDS,
    PublicNetwork,
    PublicNetworkResolver,
    split_org_field,
)


class _FakeHttp:
    """Minimal stand-in for httpx.AsyncClient returning one JSON body."""

    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self._status = status_code

    async def get(self, _url):
        class _Resp:
            status_code = self._status
            _payload = self._body

            def json(inner):
                return inner._payload

        resp = _Resp()
        resp.status_code = self._status
        resp._payload = self._body
        return resp


class _FailingHttp:
    async def get(self, _url):
        raise OSError("network unreachable")


class TestSplitOrgField:
    def test_splits_asn_from_organisation_name(self):
        asn, name = split_org_field("AS49273 COSCOM Liability Limited Company")
        assert asn == "AS49273"
        assert name == "COSCOM Liability Limited Company"

    def test_bare_name_without_asn_prefix_is_kept(self):
        asn, name = split_org_field("Deutsche Telekom AG")
        assert asn is None
        assert name == "Deutsche Telekom AG"

    def test_bare_asn_yields_no_display_name(self):
        # An AS number alone tells the user nothing, so it must not be shown as
        # if it were an operator's name.
        asn, name = split_org_field("AS13335")
        assert asn == "AS13335"
        assert name is None

    def test_empty_and_none_are_handled(self):
        assert split_org_field(None) == (None, None)
        assert split_org_field("   ") == (None, None)

    def test_absurdly_long_name_is_truncated(self):
        asn, name = split_org_field("AS1 " + ("x" * 500))
        assert asn == "AS1"
        assert len(name) <= 64


def _resolver_with(results, trading_name=None):
    """Resolver whose fetch yields the given results in order.

    The trading-name lookup is stubbed out too, and returns None unless a test
    asks for a name. Without this the resolver would reach ip-api for real,
    which would make every test here depend on a third-party service and on the
    machine having internet.
    """
    resolver = PublicNetworkResolver()
    calls = {"n": 0}

    async def fake_fetch():
        index = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[index]

    async def fake_trading_name(_http):
        return trading_name

    resolver._fetch = fake_fetch
    resolver._fetch_trading_name = fake_trading_name
    return resolver, calls


class TestResolverCaching:
    @pytest.mark.asyncio
    async def test_second_call_is_served_from_cache(self):
        good = PublicNetwork(ip="203.0.113.9", isp="Example Telecom", asn="AS64500")
        resolver, calls = _resolver_with([good])

        first = await resolver.resolve()
        second = await resolver.resolve()

        assert first.ip == second.ip == "203.0.113.9"
        assert second.isp == "Example Telecom"
        assert calls["n"] == 1, "a cached answer must not hit the network again"

    @pytest.mark.asyncio
    async def test_cache_expires_after_success_ttl(self):
        first_answer = PublicNetwork(ip="203.0.113.9", isp="Old ISP")
        second_answer = PublicNetwork(ip="198.51.100.4", isp="New ISP")
        resolver, calls = _resolver_with([first_answer, second_answer])

        await resolver.resolve()
        # Rewind the clock past the success window instead of sleeping for it.
        resolver._checked_at -= SUCCESS_TTL_SECONDS + 1
        refreshed = await resolver.resolve()

        assert calls["n"] == 2
        assert refreshed.isp == "New ISP"


class TestResolverFailureHandling:
    @pytest.mark.asyncio
    async def test_failure_keeps_last_known_good_answer(self):
        good = PublicNetwork(ip="203.0.113.9", isp="Example Telecom")
        resolver, _ = _resolver_with([good, PublicNetwork()])

        await resolver.resolve()
        resolver._checked_at -= SUCCESS_TTL_SECONDS + 1
        after_failure = await resolver.resolve()

        # A momentarily unreachable echo service says nothing about whether the
        # router's own uplink is up, so the tile must not blank.
        assert after_failure.ip == "203.0.113.9"
        assert after_failure.isp == "Example Telecom"

    @pytest.mark.asyncio
    async def test_failure_retries_sooner_than_a_success_would(self):
        resolver, calls = _resolver_with([PublicNetwork(), PublicNetwork(ip="203.0.113.9")])

        await resolver.resolve()
        # Past the failure backoff but well inside the success window: a failed
        # lookup must not suppress retries for the full fifteen minutes.
        resolver._checked_at -= FAILURE_TTL_SECONDS + 1
        recovered = await resolver.resolve()

        assert calls["n"] == 2
        assert recovered.ip == "203.0.113.9"

    @pytest.mark.asyncio
    async def test_total_failure_returns_empty_rather_than_raising(self):
        resolver, _ = _resolver_with([PublicNetwork()])
        result = await resolver.resolve()
        assert result.is_empty()
        assert result.ip is None


class TestTradingNamePreference:
    """The registry's legal entity is correct but not recognisable.

    "COSCOM Liability Limited Company" is the entity that holds AS49273; the
    operator its customers know is "Ucell", and that is the name the lookup
    sites show. The trading name comes from a plaintext source, so it is scoped
    to the display name and nothing else.
    """

    @pytest.mark.asyncio
    async def test_trading_name_replaces_the_registry_name(self):
        resolver, _ = _resolver_with([PublicNetwork(
            ip="188.113.204.70", isp="COSCOM Liability Limited Company", asn="AS49273",
        )], trading_name="Ucell")

        result = await resolver.resolve()
        assert result.isp == "Ucell"
        # The address and AS number stay with the authoritative HTTPS answer.
        assert result.ip == "188.113.204.70"
        assert result.asn == "AS49273"

    @pytest.mark.asyncio
    async def test_registry_name_is_kept_when_no_trading_name_is_available(self):
        resolver, _ = _resolver_with([PublicNetwork(
            ip="188.113.204.70", isp="COSCOM Liability Limited Company", asn="AS49273",
        )])

        result = await resolver.resolve()
        assert result.isp == "COSCOM Liability Limited Company"

    @pytest.mark.asyncio
    async def test_no_trading_name_lookup_without_an_address(self):
        # Nothing to attach a name to, and no reason to spend the request.
        resolver, _ = _resolver_with([PublicNetwork()])
        calls = []

        async def record(_http):
            calls.append(1)
            return "Ucell"

        resolver._fetch_trading_name = record

        await resolver.resolve()
        assert calls == [], "no address means nothing to name, so no request"

    @pytest.mark.asyncio
    async def test_a_hostile_trading_name_cannot_grow_without_bound(self):
        # This source is plaintext, so its answer is treated as untrusted: a
        # tampered response must not push unbounded text into the telemetry
        # frame. Exercises the real parser against a fake HTTP client.
        resolver = PublicNetworkResolver()
        name = await resolver._fetch_trading_name(_FakeHttp({"status": "success", "isp": "x" * 500}))
        assert len(name) <= _MAX_NAME_LENGTH

    @pytest.mark.asyncio
    async def test_a_failed_status_yields_no_name(self):
        resolver = PublicNetworkResolver()
        assert await resolver._fetch_trading_name(_FakeHttp({"status": "fail"})) is None

    @pytest.mark.asyncio
    async def test_an_unreachable_source_yields_no_name_rather_than_raising(self):
        resolver = PublicNetworkResolver()
        assert await resolver._fetch_trading_name(_FailingHttp()) is None
