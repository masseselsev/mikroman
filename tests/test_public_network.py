"""Public uplink identity: per-router resolution, org parsing, caching, failure.

None of these tests reach the internet - the HTTP calls are stubbed - because
the point under test is the resolver's contract, not a third party's
availability.
"""

import pytest

from backend.app.services.public_network import (
    _IPWHOIS_URL,
    _MAX_NAME_LENGTH,
    FAILURE_TTL_SECONDS,
    SUCCESS_TTL_SECONDS,
    PublicNetworkResolver,
    brand_from_domain,
    clean_trading_name,
    public_ip_or_none,
    split_org_field,
)

_IP_API_PREFIX = "http://ip-api.com/json/"


class _Resp:
    def __init__(self, body, status_code=200):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _RoutedHttp:
    """Fake httpx client answering by URL prefix, for the real parsers."""

    def __init__(self, by_prefix: dict):
        self._by_prefix = by_prefix

    async def get(self, url):
        for prefix, resp in self._by_prefix.items():
            if url.startswith(prefix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _Resp({}, 404)


class _FailingHttp:
    async def get(self, _url):
        raise OSError("network unreachable")


def _resolver(*, echo_ip=None, identity=(None, None)):
    """A resolver with its two network steps stubbed.

    ``_echo_ip`` is the container-egress lookup; ``_identity_for_ip`` is the
    per-address operator lookup. Both are counted so a test can assert the
    cache actually suppressed a second round trip.
    """
    r = PublicNetworkResolver()
    calls = {"echo": 0, "identity": 0}

    async def fake_echo(_http):
        calls["echo"] += 1
        return echo_ip

    async def fake_identity(_http, _ip):
        calls["identity"] += 1
        return identity

    r._echo_ip = fake_echo
    r._identity_for_ip = fake_identity
    return r, calls


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


class TestPublicIpOrNone:
    @pytest.mark.parametrize("value, expected", [
        ("8.8.8.8", "8.8.8.8"),
        ("109.206.139.141", "109.206.139.141"),
        ("0.0.0.0", None),          # /ip/cloud before DDNS ever succeeded
        ("192.168.1.1", None),      # RFC1918
        ("10.5.5.5", None),
        ("100.64.0.1", None),       # carrier-grade NAT
        ("127.0.0.1", None),
        ("", None),
        ("not-an-ip", None),
        (None, None),
    ])
    def test_only_a_routable_public_address_passes(self, value, expected):
        assert public_ip_or_none(value) == expected


class TestPerRouterResolution:
    @pytest.mark.asyncio
    async def test_router_hint_ip_is_used_directly_and_operator_looked_up_for_it(self):
        resolver, calls = _resolver(identity=("Ucell", "AS49273"))

        got = await resolver.resolve(router_id=2, hint_ip="109.206.139.141")

        assert got.ip == "109.206.139.141"
        assert got.isp == "Ucell"
        assert got.asn == "AS49273"
        assert calls["echo"] == 0, "the router told us its IP; no need to echo"
        assert calls["identity"] == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_echo_when_the_router_has_no_usable_public_ip(self):
        resolver, calls = _resolver(echo_ip="8.8.4.4", identity=("EchoNet", None))

        got = await resolver.resolve(router_id=1, hint_ip="0.0.0.0")

        assert got.ip == "8.8.4.4"
        assert got.isp == "EchoNet"
        assert calls["echo"] == 1

    @pytest.mark.asyncio
    async def test_cache_is_kept_per_router(self):
        resolver, calls = _resolver(identity=("Op", "AS1"))

        a = await resolver.resolve(router_id=1, hint_ip="1.1.1.1")
        b = await resolver.resolve(router_id=2, hint_ip="9.9.9.9")
        again = await resolver.resolve(router_id=1, hint_ip="1.1.1.1")

        assert a.ip == "1.1.1.1"
        assert b.ip == "9.9.9.9"
        assert again.ip == "1.1.1.1"
        assert calls["identity"] == 2, "two routers, then router 1 served from cache"

    @pytest.mark.asyncio
    async def test_success_cache_expires_after_the_ttl(self):
        resolver, calls = _resolver(identity=("Op", "AS1"))
        await resolver.resolve(router_id=1, hint_ip="1.1.1.1")
        resolver._by_router[1].checked_at -= SUCCESS_TTL_SECONDS + 1
        await resolver.resolve(router_id=1, hint_ip="1.1.1.1")
        assert calls["identity"] == 2

    @pytest.mark.asyncio
    async def test_a_failed_refresh_keeps_the_last_good_answer(self):
        resolver, _ = _resolver(echo_ip="8.8.8.8", identity=("GoodNet", "AS7"))
        first = await resolver.resolve(router_id=1)
        assert first.isp == "GoodNet"

        # Next refresh finds nothing at all (no hint, echo now blank).
        async def blank_echo(_http):
            return None
        resolver._echo_ip = blank_echo
        resolver._by_router[1].checked_at -= SUCCESS_TTL_SECONDS + 1

        after = await resolver.resolve(router_id=1)
        assert after.ip == "8.8.8.8"
        assert after.isp == "GoodNet"
        assert resolver._by_router[1].last_ok is False

    @pytest.mark.asyncio
    async def test_a_failure_retries_on_the_shorter_timer(self):
        resolver, calls = _resolver(identity=(None, None))

        async def blank_echo(_http):
            return None
        resolver._echo_ip = blank_echo

        await resolver.resolve(router_id=1)                       # fails, empty
        resolver._by_router[1].checked_at -= FAILURE_TTL_SECONDS + 1
        await resolver.resolve(router_id=1)                       # retried already
        assert calls["identity"] == 0  # no ip was ever resolved, so no identity call
        assert resolver._by_router[1].last_ok is False

    @pytest.mark.asyncio
    async def test_reset_targets_one_router_or_all(self):
        resolver, _ = _resolver(identity=("Op", "AS1"))
        await resolver.resolve(router_id=1, hint_ip="1.1.1.1")
        await resolver.resolve(router_id=2, hint_ip="9.9.9.9")

        resolver.reset(1)
        assert 1 not in resolver._by_router and 2 in resolver._by_router

        resolver.reset()
        assert resolver._by_router == {}


class TestIdentityForIp:
    @pytest.mark.asyncio
    async def test_ipwhois_domain_gives_the_brand_and_asn(self):
        resolver = PublicNetworkResolver()
        http = _RoutedHttp({
            _IPWHOIS_URL: _Resp({
                "success": True,
                "connection": {"asn": 49273, "domain": "ucell.uz",
                               "org": "Ucell Net 1",
                               "isp": "COSCOM Liability Limited Company"},
            }),
        })
        brand, asn = await resolver._identity_for_ip(http, "188.113.222.163")
        assert brand == "Ucell"
        assert asn == "AS49273"

    @pytest.mark.asyncio
    async def test_falls_back_to_ip_api_for_the_address(self):
        resolver = PublicNetworkResolver()
        http = _RoutedHttp({
            _IPWHOIS_URL: _Resp({"success": False, "message": "quota"}),
            _IP_API_PREFIX: _Resp({
                "status": "success", "isp": "CleanBrand",
                "as": "AS64500 Example Holdings",
            }),
        })
        brand, asn = await resolver._identity_for_ip(http, "8.8.8.8")
        assert brand == "CleanBrand"
        assert asn == "AS64500"

    @pytest.mark.asyncio
    async def test_a_hostile_name_cannot_grow_without_bound(self):
        resolver = PublicNetworkResolver()
        http = _RoutedHttp({
            _IPWHOIS_URL: _Resp({"success": True, "connection": {}}),
            _IP_API_PREFIX: _Resp({"status": "success", "isp": "x" * 500}),
        })
        brand, _asn = await resolver._identity_for_ip(http, "8.8.8.8")
        assert len(brand) <= _MAX_NAME_LENGTH

    @pytest.mark.asyncio
    async def test_an_unreachable_source_yields_nothing_rather_than_raising(self):
        resolver = PublicNetworkResolver()
        assert await resolver._identity_for_ip(_FailingHttp(), "8.8.8.8") == (None, None)


class TestBrandFromDomain:
    @pytest.mark.parametrize("domain, expected", [
        ("ucell.uz", "Ucell"),
        ("UCELL.UZ", "Ucell"),
        ("bt.co.uk", "Bt"),
        ("t-mobile.com", "T-Mobile"),
        ("mts.com.ua", "Mts"),
        ("comcast.net", "Comcast"),
    ])
    def test_registrable_label_becomes_the_brand(self, domain, expected):
        assert brand_from_domain(domain) == expected

    @pytest.mark.parametrize("bad", [
        None, "", "   ", "localhost", "uz", "1.2.3.4",
        "not a domain", "<script>.com", "a.b",
    ])
    def test_rubbish_yields_none(self, bad):
        # "a.b" -> label "a" is too short to be a brand.
        assert brand_from_domain(bad) is None


class TestCleanTradingName:
    @pytest.mark.parametrize("raw, expected", [
        ("Ucell Net 1", "Ucell"),
        ("Ucell Network", "Ucell"),
        ("Deutsche Telekom", "Deutsche Telekom"),
        ("Orange 42", "Orange"),
    ])
    def test_trailing_clutter_is_removed(self, raw, expected):
        assert clean_trading_name(raw) == expected

    @pytest.mark.parametrize("legal", [
        "COSCOM Liability Limited Company",
        "Example Telecom LLC",
        "Foo Bar Ltd",
        "Bar Inc.",
        "", None,
    ])
    def test_legal_entities_are_rejected(self, legal):
        assert clean_trading_name(legal) is None
