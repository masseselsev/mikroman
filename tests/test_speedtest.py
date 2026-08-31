"""WAN speed tests driven through a RouterOS container.

The parser carries the risk here. Ookla's human-readable output has changed
shape between CLI releases and spacing differs between builds, so the rule is:
match every field independently, skip what is not understood, and never discard
a run because one line failed. These tests pin real output shapes.
"""
import pytest

from backend.app.services.speedtest import (
    CONTAINER_COMMENT,
    DEFAULT_IMAGE,
    SpeedTestRunner,
    parse_speedtest_output,
)

# Output as the Ookla CLI writes it inside the tangentsoft/speedtest-cli image.
OOKLA_OUTPUT = """
   Speedtest by Ookla

      Server: Some ISP - Riga (id: 12345)
         ISP: Example Telecom
Idle Latency:     8.42 ms   (jitter: 0.51ms, low: 8.10ms, high: 9.30ms)
    Download:   482.17 Mbps (data used: 512.4 MB)
      Upload:    93.66 Mbps (data used: 101.2 MB)
 Packet Loss:     0.0%
  Result URL: https://www.speedtest.net/result/c/abc-123
""".strip().split("\n")

# An older build: no jitter on the latency line, "Ping" instead of "Latency".
OLDER_OUTPUT = """
Testing download speed
    Download:   95.31 Mbps
      Upload:   19.02 Mbps
        Ping:   23.10 ms
""".strip().split("\n")


class TestParsing:
    def test_reads_every_field_from_current_output(self):
        r = parse_speedtest_output(OOKLA_OUTPUT)
        assert r.status == "ok"
        assert r.download_mbps == pytest.approx(482.17)
        assert r.upload_mbps == pytest.approx(93.66)
        assert r.ping_ms == pytest.approx(8.42)
        assert r.jitter_ms == pytest.approx(0.51)
        assert r.packet_loss_pct == pytest.approx(0.0)
        assert r.server_name == "Some ISP - Riga (id: 12345)"
        assert r.isp == "Example Telecom"
        assert r.result_url == "https://www.speedtest.net/result/c/abc-123"

    def test_a_build_without_the_extra_lines_still_yields_speeds(self):
        """Missing jitter, ISP, server and result URL must not fail the run."""
        r = parse_speedtest_output(OLDER_OUTPUT)
        assert r.status == "ok"
        assert r.download_mbps == pytest.approx(95.31)
        assert r.upload_mbps == pytest.approx(19.02)
        assert r.jitter_ms is None
        assert r.isp is None
        assert r.result_url is None

    def test_a_data_used_suffix_is_not_mistaken_for_the_speed(self):
        r = parse_speedtest_output(["    Download:   482.17 Mbps (data used: 512.4 MB)"])
        assert r.download_mbps == pytest.approx(482.17)

    def test_output_that_says_nothing_useful_is_a_failure_not_a_zero(self):
        """Reporting 0 Mbps would look like a measurement. It is not one."""
        r = parse_speedtest_output(["container startup", "some unrelated noise"])
        assert r.status == "failed"
        assert r.download_mbps is None
        assert r.error

    def test_empty_output_is_a_failure(self):
        r = parse_speedtest_output([])
        assert r.status == "failed"
        assert r.raw_output == ""

    def test_raw_output_is_kept_so_a_parser_change_can_be_checked(self):
        r = parse_speedtest_output(OOKLA_OUTPUT)
        assert "Speedtest by Ookla" in r.raw_output


class FakeClient:
    """Stands in for the RouterOS client; records what was asked of it."""

    def __init__(self, containers=None, logging_rules=None, log_entries=None):
        self.containers = containers if containers is not None else []
        self.logging_rules = logging_rules if logging_rules is not None else []
        self.log_entries = log_entries if log_entries is not None else []
        self.started = []
        self.added_logging = []
        self.added_containers = []

    async def get_containers(self):
        return list(self.containers)

    async def get_logging_rules(self):
        return list(self.logging_rules)

    async def add_logging_rule(self, topics, action="memory"):
        self.added_logging.append((topics, action))
        self.logging_rules.append({".id": "*9", "topics": topics, "action": action})
        return "*9"

    async def add_container(self, payload):
        self.added_containers.append(payload)
        return {".id": "*1"}

    async def container_command(self, action, container_id):
        self.started.append((action, container_id))
        return True

    async def get_log(self, topics=None, limit=300):
        return list(self.log_entries)


def _log(entries):
    return [{".id": f"*{i}", "topics": "container,info", "message": m}
            for i, m in enumerate(entries)]


class TestContainerDiscovery:
    @pytest.mark.asyncio
    async def test_our_container_is_found_by_its_comment(self):
        client = FakeClient(containers=[
            {".id": "*1", "comment": "someone else's thing"},
            {".id": "*2", "comment": CONTAINER_COMMENT},
        ])
        found = await SpeedTestRunner(client).find_container()
        assert found[".id"] == "*2"

    @pytest.mark.asyncio
    async def test_a_hand_made_speedtest_container_is_accepted_too(self):
        """Telling an operator their working container is the wrong one, because
        it lacks our comment, would be obtuse."""
        client = FakeClient(containers=[
            {".id": "*3", "comment": "", "remote-image": "quay.io/tangent/speedtest-cli:latest"},
        ])
        found = await SpeedTestRunner(client).find_container()
        assert found[".id"] == "*3"

    @pytest.mark.asyncio
    async def test_unrelated_containers_are_never_touched(self):
        client = FakeClient(containers=[{".id": "*4", "comment": "", "remote-image": "nginx"}])
        assert await SpeedTestRunner(client).find_container() is None

    @pytest.mark.asyncio
    async def test_creating_it_does_not_ask_for_start_on_boot(self):
        """The image runs once and exits, so starting it at boot would produce
        one stray result per reboot and nothing else."""
        client = FakeClient()
        await SpeedTestRunner(client).create_container(interface="veth1", root_dir="usb1/st")
        payload = client.added_containers[0]
        assert payload["remote-image"] == DEFAULT_IMAGE
        assert payload["start-on-boot"] == "no"
        assert payload["logging"] == "yes"
        assert payload["comment"] == CONTAINER_COMMENT


class TestLogging:
    @pytest.mark.asyncio
    async def test_the_container_topic_is_added_when_missing(self):
        """RouterOS ships no container logging action, so without this the test
        runs correctly and its output goes nowhere."""
        client = FakeClient(logging_rules=[{".id": "*1", "topics": "info", "action": "memory"}])
        assert await SpeedTestRunner(client).ensure_logging() is True
        assert client.added_logging == [("container", "memory")]

    @pytest.mark.asyncio
    async def test_an_existing_rule_is_left_alone(self):
        client = FakeClient(logging_rules=[
            {".id": "*1", "topics": "container,info", "action": "memory", "disabled": "false"},
        ])
        assert await SpeedTestRunner(client).ensure_logging() is True
        assert client.added_logging == []

    @pytest.mark.asyncio
    async def test_a_disabled_rule_does_not_count(self):
        client = FakeClient(logging_rules=[
            {".id": "*1", "topics": "container", "action": "memory", "disabled": "true"},
        ])
        await SpeedTestRunner(client).ensure_logging()
        assert client.added_logging == [("container", "memory")]


class TestRunning:
    @pytest.mark.asyncio
    async def test_a_run_starts_the_container_and_parses_what_it_logged(self):
        client = FakeClient(
            containers=[{".id": "*2", "comment": CONTAINER_COMMENT}],
            logging_rules=[{".id": "*1", "topics": "container", "action": "memory"}],
            # One unrelated line already in the ring buffer, as on a real router.
            log_entries=_log(["veth1 link up"]),
        )
        fresh = [{".id": f"*run{i}", "topics": "container,info", "message": m}
                 for i, m in enumerate(OOKLA_OUTPUT)]

        async def get_log(topics=None, limit=300):
            # Output appears only after the container has been started.
            return list(client.log_entries) + (fresh if client.started else [])

        client.get_log = get_log
        reading = await SpeedTestRunner(client).run(timeout_seconds=5, poll_interval=0.01)
        assert client.started == [("start", "*2")]
        assert reading.status == "ok"
        assert reading.download_mbps == pytest.approx(482.17)

    @pytest.mark.asyncio
    async def test_output_already_in_the_log_is_not_read_as_this_run_s_result(self):
        """A previous run's numbers are still in the ring buffer. Returning them
        instantly would report a stale measurement as a fresh one."""
        stale = _log(OOKLA_OUTPUT)
        client = FakeClient(
            containers=[{".id": "*2", "comment": CONTAINER_COMMENT}],
            logging_rules=[{".id": "*1", "topics": "container", "action": "memory"}],
            log_entries=stale,
        )
        reading = await SpeedTestRunner(client).run(timeout_seconds=0.2, poll_interval=0.01)
        assert reading.status == "timeout"
        assert reading.download_mbps is None

    @pytest.mark.asyncio
    async def test_no_container_is_reported_rather_than_raised(self):
        client = FakeClient(containers=[])
        reading = await SpeedTestRunner(client).run(timeout_seconds=1, poll_interval=0.01)
        assert reading.status == "failed"
        assert "container" in reading.error.lower()

    @pytest.mark.asyncio
    async def test_a_partial_result_after_a_timeout_is_kept(self):
        """Download measured, upload never arrived: better than discarding both."""
        partial = _log(["    Download:   482.17 Mbps"])
        client = FakeClient(
            containers=[{".id": "*2", "comment": CONTAINER_COMMENT}],
            logging_rules=[{".id": "*1", "topics": "container", "action": "memory"}],
        )

        async def get_log(topics=None, limit=300):
            # Empty before the run's marker is taken, then the partial output.
            client.log_entries = partial
            return list(partial) if client.started else []

        client.get_log = get_log
        reading = await SpeedTestRunner(client).run(timeout_seconds=0.2, poll_interval=0.01)
        assert reading.status == "ok"
        assert reading.download_mbps == pytest.approx(482.17)
        assert reading.upload_mbps is None
        assert "partial" in reading.error.lower()

    @pytest.mark.asyncio
    async def test_a_router_that_refuses_the_start_is_reported_not_raised(self):
        client = FakeClient(
            containers=[{".id": "*2", "comment": CONTAINER_COMMENT}],
            logging_rules=[{".id": "*1", "topics": "container", "action": "memory"}],
        )

        async def boom(action, container_id):
            raise RuntimeError("no such command")

        client.container_command = boom
        reading = await SpeedTestRunner(client).run(timeout_seconds=1, poll_interval=0.01)
        assert reading.status == "failed"
        assert "no such command" in reading.error


def test_speedtest_routes_are_registered():
    from backend.app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/routers/{router_id}/speedtest" in paths
    assert "/api/v1/routers/{router_id}/speedtest/run" in paths
    assert "/api/v1/routers/{router_id}/speedtest/container" in paths
    assert "/api/v1/routers/{router_id}/speedtest/history" in paths
