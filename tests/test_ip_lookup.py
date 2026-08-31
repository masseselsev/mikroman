"""External IP-lookup services: template validation, storage and resolution.

The validation tests carry the most weight here. A URL template is user input
that is stored, replayed and finally used as the ``href`` of a link the user
clicks, so a template that survives validation is a template the browser will
navigate to.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base
from backend.app.services.ip_lookup import (
    BUILTIN_SERVICES,
    DEFAULT_SERVICE_ID,
    IP_PLACEHOLDER,
    IpLookupConfig,
    IpLookupService,
    TemplateError,
    build_lookup_url,
    get_config,
    resolve_config,
    save_config,
    validate_template,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


class TestTemplateValidationRejectsDangerousSchemes:
    """These are the cases that would execute in the page's origin."""

    @pytest.mark.parametrize("template", [
        "javascript:alert(document.cookie)//{ip}",
        "javascript:fetch('/api/v1/routers').then(r=>r.text()).then(console.log)//{ip}",
        "JaVaScRiPt:alert(1)//{ip}",
        "data:text/html,<script>alert('{ip}')</script>",
        "vbscript:msgbox('{ip}')",
        "file:///etc/passwd?{ip}",
    ])
    def test_non_http_schemes_are_refused(self, template):
        with pytest.raises(TemplateError):
            validate_template(template)

    def test_placeholder_cannot_smuggle_a_scheme_past_the_check(self):
        # Checking the scheme on the raw template, before substitution, can be
        # fooled by a placeholder sitting in front of the colon. Validation
        # substitutes first for exactly this reason.
        with pytest.raises(TemplateError):
            validate_template("{ip}javascript:alert(1)")

    def test_embedded_credentials_are_refused(self):
        # Following the link would hand these to the remote site.
        with pytest.raises(TemplateError):
            validate_template("https://user:secret@example.com/{ip}")


class TestTemplateValidationRejectsUnusableTemplates:
    def test_missing_placeholder(self):
        with pytest.raises(TemplateError, match="placeholder"):
            validate_template("https://2ip.io/")

    def test_empty_and_none(self):
        for value in (None, "", "   "):
            with pytest.raises(TemplateError):
                validate_template(value)

    def test_missing_host(self):
        with pytest.raises(TemplateError):
            validate_template("https:///{ip}")

    def test_absurdly_long_template(self):
        with pytest.raises(TemplateError):
            validate_template("https://example.com/" + "x" * 600 + "/{ip}")


class TestTemplateValidationAcceptsRealTemplates:
    @pytest.mark.parametrize("template", [
        "https://2ip.io/{ip}/",
        "http://internal-tool.lan/lookup?addr={ip}",
        "https://bgp.he.net/ip/{ip}",
        "https://example.com/a/{ip}/b/{ip}",
    ])
    def test_accepted(self, template):
        assert validate_template(template) == template

    def test_surrounding_whitespace_is_trimmed(self):
        assert validate_template("  https://2ip.io/{ip}/  ") == "https://2ip.io/{ip}/"

    def test_every_builtin_passes_its_own_validation(self):
        for service in BUILTIN_SERVICES:
            assert validate_template(service.url_template) == service.url_template
            assert IP_PLACEHOLDER in service.url_template


class TestBuildLookupUrl:
    def test_substitutes_the_address(self):
        assert build_lookup_url("https://2ip.io/ip/{ip}/", "188.113.204.70") == "https://2ip.io/ip/188.113.204.70/"

    def test_the_2ip_builtin_points_at_the_path_that_actually_resolves(self):
        # The first release shipped https://2ip.io/{ip}/, which 404s. The site
        # blocks non-browser clients, so this was confirmed by hand and is
        # pinned here rather than left to be rediscovered.
        service = next(s for s in BUILTIN_SERVICES if s.id == "2ip")
        assert service.url_template == "https://2ip.io/ip/{ip}/"
        assert build_lookup_url(service.url_template, "8.8.8.8") == "https://2ip.io/ip/8.8.8.8/"

    def test_ipv6_is_percent_encoded(self):
        url = build_lookup_url("https://ipinfo.io/{ip}", "2001:db8::1")
        assert "2001%3Adb8%3A%3A1" in url

    def test_hostile_address_cannot_break_out_of_the_path(self):
        # The address comes from an external echo service, so it is untrusted.
        url = build_lookup_url("https://2ip.io/{ip}/", "1.2.3.4/../../evil?x=<script>")
        assert "<script>" not in url
        assert url.startswith("https://2ip.io/")

    def test_substitution_result_is_revalidated(self):
        with pytest.raises(TemplateError):
            build_lookup_url("{ip}", "https://evil.example")


class TestResolveConfig:
    def test_default_falls_back_when_it_points_at_a_deleted_service(self):
        config = IpLookupConfig(enabled_ids=["ipinfo"], default_id="gone-custom", custom=[])
        resolved = resolve_config(config)
        assert resolved.default_id == "ipinfo"

    def test_enabling_nothing_still_leaves_something_to_click(self):
        resolved = resolve_config(IpLookupConfig(enabled_ids=[], default_id="whatever"))
        assert resolved.enabled_ids == [DEFAULT_SERVICE_ID]
        assert resolved.default_id == DEFAULT_SERVICE_ID

    def test_unknown_ids_are_dropped(self):
        resolved = resolve_config(IpLookupConfig(enabled_ids=["ipinfo", "not-a-service"], default_id="ipinfo"))
        assert resolved.enabled_ids == ["ipinfo"]


class TestPersistence:
    @pytest.mark.asyncio
    async def test_defaults_when_nothing_saved(self, session):
        config = await get_config(session)
        assert config.default_id == DEFAULT_SERVICE_ID
        assert config.enabled_ids == [DEFAULT_SERVICE_ID]

    @pytest.mark.asyncio
    async def test_round_trip_with_a_custom_service(self, session):
        await save_config(session, IpLookupConfig(
            enabled_ids=["ipinfo", "my_tool"],
            default_id="my_tool",
            custom=[IpLookupService(id="my_tool", name="My Tool", url_template="https://tool.lan/{ip}")],
        ))
        loaded = await get_config(session)
        assert loaded.default_id == "my_tool"
        assert [s.id for s in loaded.custom] == ["my_tool"]
        assert loaded.custom[0].url_template == "https://tool.lan/{ip}"

    @pytest.mark.asyncio
    async def test_saving_a_dangerous_custom_template_is_refused(self, session):
        with pytest.raises(TemplateError):
            await save_config(session, IpLookupConfig(
                enabled_ids=["evil"],
                default_id="evil",
                custom=[IpLookupService(id="evil", name="Evil", url_template="javascript:alert(1)//{ip}")],
            ))

    @pytest.mark.asyncio
    async def test_custom_id_cannot_shadow_a_builtin(self, session):
        with pytest.raises(TemplateError, match="Duplicate"):
            await save_config(session, IpLookupConfig(
                enabled_ids=["ipinfo"],
                default_id="ipinfo",
                custom=[IpLookupService(id="ipinfo", name="Impostor", url_template="https://evil.example/{ip}")],
            ))

    @pytest.mark.asyncio
    async def test_corrupt_stored_json_falls_back_to_defaults(self, session):
        from backend.app.db.models import AppSetting
        from backend.app.services.ip_lookup import SETTING_KEY
        session.add(AppSetting(key=SETTING_KEY, value="{not json"))
        await session.commit()
        config = await get_config(session)
        assert config.default_id == DEFAULT_SERVICE_ID

    @pytest.mark.asyncio
    async def test_a_stored_entry_that_no_longer_validates_is_skipped_not_fatal(self, session):
        # A setting can predate a tightening of the rules; one bad row must not
        # stop the settings page from loading.
        import json

        from backend.app.db.models import AppSetting
        from backend.app.services.ip_lookup import SETTING_KEY
        session.add(AppSetting(key=SETTING_KEY, value=json.dumps({
            "enabled_ids": ["ipinfo", "bad"],
            "default_id": "ipinfo",
            "custom": [
                {"id": "bad", "name": "Bad", "url_template": "javascript:alert(1)//{ip}"},
                {"id": "good", "name": "Good", "url_template": "https://good.example/{ip}"},
            ],
        })))
        await session.commit()

        config = await get_config(session)
        assert [s.id for s in config.custom] == ["good"]
        assert "bad" not in config.enabled_ids
