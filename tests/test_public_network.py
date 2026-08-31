"""Public uplink identity: org-string parsing, caching and failure behaviour.

None of these tests reach the internet - the fetch is stubbed - because the
point under test is the resolver's contract, not a third-party service's
availability.
"""

import pytest

from backend.app.services.public_network import (
    FAILURE_TTL_SECONDS,
    SUCCESS_TTL_SECONDS,
    PublicNetwork,
    PublicNetworkResolver,
    split_org_field,
)


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


def _resolver_with(results):
    """Resolver whose fetch yields the given results in order."""
    resolver = PublicNetworkResolver()
    calls = {"n": 0}

    async def fake_fetch():
        index = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[index]

    resolver._fetch = fake_fetch
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
