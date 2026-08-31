"""WAN speed tests run from a container on the router itself.

Why a container
---------------
RouterOS has no internet speed test. ``/tool/speed-test`` and
``/tool/bandwidth-test`` both measure against *another RouterOS device*, which
answers a different question than "what is my line actually doing". Ookla's
``speedtest`` CLI does answer it, and RouterOS 7.4+ can run OCI containers, so
the test runs on the router - measuring the router's own WAN link, not the link
between the router and whatever machine MikroMan happens to be installed on.

How it is driven, given the REST API's limits
---------------------------------------------
There is no ``docker exec`` over REST: ``/container/shell`` exists but is an
interactive console command. The speedtest image is built to run once and exit,
which turns out to fit the REST surface exactly:

1. ``POST /container/start`` with the container's id - it runs and exits.
2. Its stdout reaches the RouterOS log, provided a ``container`` logging action
   exists (RouterOS does not create one by default, so :meth:`ensure_logging`
   adds it).
3. ``GET /log`` is polled for lines on the ``container`` topic newer than the
   marker taken before the start.

So the whole cycle is three ordinary REST calls and some patience. Nothing here
requires SSH or the console.

The parser is deliberately tolerant. Ookla's human-readable output has changed
format between CLI releases and the exact spacing differs between builds, so
every field is matched independently and a line that cannot be understood is
skipped rather than failing the run. A test that produced a download figure and
nothing else is still worth recording.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mikroman.speedtest")

# The image the operator is offered. Purpose-built for RouterOS: 2.7 MiB,
# published for arm64 and amd64, runs once and exits.
DEFAULT_IMAGE = "quay.io/tangent/speedtest-cli:latest"
# Marks the container MikroMan manages, so an operator's own speedtest container
# is never started, stopped or removed by us.
CONTAINER_COMMENT = "mikroman:speedtest"
# RouterOS logging topic the container's stdout arrives on.
LOG_TOPIC = "container"
# A full Ookla run is ~15s of transfer plus server selection. Beyond this the
# run is reported as a timeout rather than left pending forever.
DEFAULT_TIMEOUT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 2.0


@dataclass
class SpeedTestReading:
    """What one run measured. Every field is optional - see the module docstring."""
    download_mbps: Optional[float] = None
    upload_mbps: Optional[float] = None
    ping_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None
    server_name: Optional[str] = None
    isp: Optional[str] = None
    result_url: Optional[str] = None
    raw_output: str = ""
    status: str = "ok"
    error: Optional[str] = None
    lines: List[str] = field(default_factory=list)

    @property
    def has_any_figure(self) -> bool:
        return any(
            v is not None for v in
            (self.download_mbps, self.upload_mbps, self.ping_ms)
        )


# Ookla reports speeds in Mbps and may append a data-used note; latency lines
# carry a jitter figure in parentheses on newer builds. Each is matched on its
# own so a change to one line cannot break the others.
_PATTERNS = {
    "download_mbps": re.compile(r"Download:\s*([\d.]+)\s*Mbps", re.I),
    "upload_mbps": re.compile(r"Upload:\s*([\d.]+)\s*Mbps", re.I),
    "ping_ms": re.compile(r"(?:Idle )?Latency:\s*([\d.]+)\s*ms", re.I),
    "jitter_ms": re.compile(r"jitter:\s*([\d.]+)\s*ms", re.I),
    "packet_loss_pct": re.compile(r"Packet Loss:\s*([\d.]+)\s*%", re.I),
}
_SERVER = re.compile(r"Server:\s*(.+?)\s*$", re.I)
_ISP = re.compile(r"ISP:\s*(.+?)\s*$", re.I)
_RESULT_URL = re.compile(r"(https?://\S*speedtest\.net/result\S*)", re.I)
# Some builds emit "Result URL: ..." without the speedtest.net host.
_ANY_RESULT_URL = re.compile(r"Result\s*URL:\s*(\S+)", re.I)


def parse_speedtest_output(lines: List[str]) -> SpeedTestReading:
    """Pull figures out of Ookla CLI output, skipping anything unrecognised."""
    reading = SpeedTestReading(raw_output="\n".join(lines), lines=list(lines))

    for line in lines:
        for attr, pattern in _PATTERNS.items():
            if getattr(reading, attr) is None:
                match = pattern.search(line)
                if match:
                    try:
                        setattr(reading, attr, float(match.group(1)))
                    except ValueError:
                        pass
        if reading.server_name is None and "Server:" in line:
            match = _SERVER.search(line)
            if match:
                reading.server_name = match.group(1)[:200]
        if reading.isp is None and "ISP:" in line:
            match = _ISP.search(line)
            if match:
                reading.isp = match.group(1)[:200]
        if reading.result_url is None:
            match = _RESULT_URL.search(line) or _ANY_RESULT_URL.search(line)
            if match:
                reading.result_url = match.group(1)[:300]

    if not reading.has_any_figure:
        reading.status = "failed"
        reading.error = "Container produced no recognisable speed test output."
    return reading


class SpeedTestRunner:
    """Drives the speedtest container and reads its result back out of the log."""

    def __init__(self, client: Any):
        self.client = client

    # --- container lifecycle ------------------------------------------------

    async def find_container(self) -> Optional[Dict[str, Any]]:
        """The speedtest container MikroMan manages, if it has been created.

        Identified by our comment first. Falling back to the image name lets an
        operator who created the container by hand still use the button, which
        is friendlier than telling them their working container is the wrong one.
        """
        containers = await self.client.get_containers()
        for raw in containers:
            if (raw.get("comment") or "") == CONTAINER_COMMENT:
                return raw
        for raw in containers:
            if "speedtest" in (raw.get("remote-image") or raw.get("image") or "").lower():
                return raw
        return None

    async def ensure_logging(self) -> bool:
        """Make sure container output reaches the log we read results from.

        RouterOS ships no ``container`` logging action, so without this the
        container runs correctly and its output goes nowhere. Returns True when
        a rule is in place afterwards.
        """
        try:
            rules = await self.client.get_logging_rules()
        except Exception as e:
            logger.warning(f"Could not read logging rules: {e}")
            return False

        for rule in rules:
            topics = (rule.get("topics") or "").lower()
            if LOG_TOPIC in topics and (rule.get("disabled") or "false") != "true":
                return True

        try:
            await self.client.add_logging_rule(topics=LOG_TOPIC, action="memory")
            logger.info("Added a 'container' logging action so speed test output is readable")
            return True
        except Exception as e:
            logger.warning(f"Could not add container logging rule: {e}")
            return False

    async def create_container(
        self, *, interface: str, root_dir: str, image: str = DEFAULT_IMAGE
    ) -> Dict[str, Any]:
        """Create the speedtest container.

        ``start-on-boot`` is deliberately off: the image runs once and exits, so
        starting it at boot would produce one stray result per reboot and
        nothing else.
        """
        return await self.client.add_container({
            "remote-image": image,
            "interface": interface,
            "root-dir": root_dir,
            "comment": CONTAINER_COMMENT,
            "logging": "yes",
            "start-on-boot": "no",
        })

    # --- running ------------------------------------------------------------

    async def run(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> SpeedTestReading:
        """Start the container, wait for it to finish, and parse what it logged."""
        container = await self.find_container()
        if container is None:
            return SpeedTestReading(
                status="failed",
                error="No speed test container on this router yet.",
            )

        await self.ensure_logging()

        # Everything already in the log is somebody else's; only lines after
        # this marker belong to the run we are about to start. Using the newest
        # existing id rather than a timestamp avoids depending on the router's
        # clock agreeing with ours.
        try:
            before = await self._container_log_ids()
        except Exception as e:
            logger.warning(f"Could not read the log before starting: {e}")
            before = set()

        try:
            await self.client.container_command("start", container[".id"])
        except Exception as e:
            return SpeedTestReading(status="failed", error=f"Could not start the container: {e}")

        return await self._await_result(before, timeout_seconds, poll_interval)

    async def _container_log_ids(self) -> set:
        entries = await self.client.get_log(topics=LOG_TOPIC)
        return {e.get(".id") for e in entries if e.get(".id")}

    async def _await_result(
        self, before_ids: set, timeout_seconds: float, poll_interval: float
    ) -> SpeedTestReading:
        """Poll the log until the run has produced a figure, or time runs out.

        Returns as soon as both a download and an upload figure are present
        rather than waiting for the container to be observed stopped: the
        container's state flips back to stopped the moment it exits, and racing
        that transition would sometimes miss it entirely.
        """
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        newest: List[str] = []

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(poll_interval)
            try:
                entries = await self.client.get_log(topics=LOG_TOPIC)
            except Exception as e:
                logger.debug(f"Log poll failed, will retry: {e}")
                continue

            newest = [
                e.get("message") or ""
                for e in entries
                if e.get(".id") not in before_ids
            ]
            if not newest:
                continue

            reading = parse_speedtest_output(newest)
            if reading.download_mbps is not None and reading.upload_mbps is not None:
                return reading

        # Out of time. Whatever was logged is still worth keeping and parsing -
        # a run that measured download but timed out on upload beats nothing.
        reading = parse_speedtest_output(newest)
        if reading.has_any_figure:
            reading.status = "ok"
            reading.error = "Timed out before the test finished; figures are partial."
        else:
            reading.status = "timeout"
            reading.error = (
                f"No speed test output within {timeout_seconds:.0f}s. Check that the "
                f"container has an internet-facing veth interface and that the "
                f"'container' logging topic is enabled."
            )
        return reading
