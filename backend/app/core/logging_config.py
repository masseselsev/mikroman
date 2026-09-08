"""Logging setup: a persistent file in the data directory, plus a quiet transport.

Why this module exists
----------------------
MikroMan can run as a RouterOS container. There the only sink outside the
container is the router's own log ring, which RouterOS feeds from the
container's stdout when the container is created with ``logging=yes``. That is
worth keeping - it is the only way a crash traceback survives the process - but
it made the app the loudest thing on the device: measured on a running hAP be3
Media, **995 of the 1000 lines in the router's log ring were MikroMan's own**
(742 ``httpx`` request lines, 236 uvicorn access lines) and 5 were real device
events. Two consequences, both bad:

* the ring turns over in about five minutes, so genuine events are pushed out
  before the 60-second scraper can copy them to the database, and
* every one of those lines is written to the router's memory, read back over
  REST, classified and inserted into SQLite, then pruned again - hundreds of row
  writes a minute that exist only to echo the app at itself.

So the app logs to a rotating file inside the data directory (``/data`` in the
container, i.e. on the USB stick the database lives on) which survives restarts
and is readable through ``GET /api/v1/logs?source=app``, and it stops narrating
each individual HTTP request in either place.

Levels chosen
-------------
``httpx`` / ``httpcore`` at WARNING
    One INFO line per request is a transaction log, not a diagnostic. The
    request *failures* are what matter and they are WARNING or above.
``uvicorn.access`` at WARNING
    Same argument for inbound requests; the router already records real
    authentications in its own log.
Everything else at INFO
    Startup, collection faults (logged once per distinct fault, not per tick),
    alerts.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

from backend.app.core.config import settings

LOGGER_NAME = "mikroman"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_NOISY_LOGGERS = ("httpx", "httpcore", "uvicorn.access", "aiogram", "hpack", "urllib3")

#: Module-level so the API can report where the file actually is (and why not,
#: if it could not be opened).
_state: dict = {"file_path": None, "error": None}


def resolve_log_dir() -> Path:
    """The directory that holds the app's long-lived state.

    Derived from the database URL rather than from a fixed ``/data`` because the
    same build runs in a container (``sqlite+aiosqlite:////data/app.db``), in
    docker-compose with a named volume, and from a source checkout
    (``./data/app.db``). The log belongs next to the database it describes so a
    backup of one gets the other.
    """
    override = os.environ.get("MIKROMAN_DATA_DIR", "").strip()
    if override:
        return Path(override)
    url = settings.DATABASE_URL or ""
    if "sqlite" in url:
        # sqlite+aiosqlite:////data/app.db -> /data/app.db ; sqlite:///./data/app.db -> ./data/app.db
        path = url.split(":///")[-1]
        parent = Path(path).parent
        if str(parent):
            return parent
    return Path("data")


def log_file_path() -> Optional[Path]:
    """Where the persistent log lives, or None if it could not be opened."""
    return _state.get("file_path")


def log_file_error() -> Optional[str]:
    return _state.get("error")


def _build_file_handler(log_dir: Path) -> RotatingFileHandler:
    """A bounded rotating handler.

    Size-capped because this sits on flash: without a cap the log grows until
    the stick is full, and a full stick takes the database down with it. The
    total footprint is ``max_bytes * (backups + 1)``.
    """
    handler = RotatingFileHandler(
        log_dir / "mikroman.log",
        maxBytes=settings.LOG_FILE_MAX_BYTES,
        backupCount=settings.LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
        # Open on first record: importing this module (which tests do) must not
        # create files, and a read-only volume must not produce an empty log.
        delay=True,
    )
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    return handler


class _BelowWarningFilter:
    """Drop every record under WARNING on a logger this app has quieted.

    Level alone would do the job today: uvicorn applies its ``dictConfig`` in
    ``Config.__init__``, before the app module is imported, so the levels set
    here win. That is an ordering fact about one server, not a property of the
    code - ``dictConfig`` rewrites ``level`` and leaves an already-attached filter
    in place unless the config names one. The filter is what makes the silence
    hold regardless of who configures logging after this ran.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


def _quiet_logger(name: str, quiet: bool) -> None:
    """Set ``name`` to WARNING (or back to DEBUG) and add/remove the filter."""
    noisy = logging.getLogger(name)
    noisy.setLevel(logging.DEBUG if settings.DEBUG else (logging.WARNING if quiet else logging.NOTSET))
    ours = [f for f in noisy.filters if getattr(f, "_mikroman_quiet", False)]
    for existing in ours:
        noisy.filters.remove(existing)
    if quiet:
        marker = _BelowWarningFilter()
        marker._mikroman_quiet = True  # type: ignore[attr-defined]
        # logging.Logger.addFilter appends and keeps the call order of the rest.
        noisy.addFilter(marker)


def configure_logging() -> Optional[Path]:
    """Install console + persistent-file logging and silence the per-request noise.

    Idempotent: calling it twice (an import plus a test fixture, say) replaces
    this app's own handlers instead of stacking duplicates that would double
    every line.

    Returns the log file path, or None when file logging is unavailable.
    """
    root = logging.getLogger()
    level = logging.DEBUG if settings.DEBUG else logging.INFO

    if not any(getattr(h, "_mikroman_console", False) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(FILE_FORMAT))
        console._mikroman_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    for handler in list(root.handlers):
        if getattr(handler, "_mikroman_console", False):
            handler.setLevel(level)

    root.setLevel(level)

    existing = [h for h in root.handlers if getattr(h, "_mikroman_file", False)]
    if settings.LOG_TO_FILE:
        log_dir = resolve_log_dir()
        path = log_dir / "mikroman.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            # Probe by opening rather than by trusting the handler constructor:
            # RotatingFileHandler is created with delay=True (it must not
            # truncate or recreate anything on import), so an unwritable
            # directory would otherwise raise on the first log line, from
            # inside the logging machinery, far away from the reason.
            with open(path, "a", encoding="utf-8"):
                pass
            # Reuse the open handler only while it points at this same file. A
            # data directory that moved - MIKROMAN_DATA_DIR, or a different
            # DATABASE_URL in a test - has to rebuild it, or the app keeps
            # writing where the *previous* configuration said.
            stale = [h for h in existing if Path(h.baseFilename).resolve() != path.resolve()]
            for handler in stale:
                root.removeHandler(handler)
                handler.close()
            live = [h for h in existing if h not in stale]
            handler = live[0] if live else _build_file_handler(log_dir)
            handler.setLevel(level)
            handler._mikroman_file = True  # type: ignore[attr-defined]
            if not live:
                root.addHandler(handler)
            _state["file_path"] = path
            _state["error"] = None
        except OSError as e:
            # Read-only volume, full flash, or a permission the directory does
            # not grant. The console sink is still there, so the app must keep
            # serving - say so once, loudly, and carry on.
            _state["error"] = f"{e.__class__.__name__}: {e}"
            _state["file_path"] = None
            for handler in existing:
                root.removeHandler(handler)
                handler.close()
            logging.getLogger(LOGGER_NAME).warning(
                f"Persistent log file disabled ({_state['error']}); "
                f"logging to console only."
            )
    else:
        for handler in existing:
            root.removeHandler(handler)
            handler.close()
        _state["file_path"] = None

    # Each of these logs one line per request or per Telegram update, which is a
    # transaction log rather than a diagnostic; their failures are WARNING or
    # above and stay visible. DEBUG keeps everything for a debugging session.
    for name in _NOISY_LOGGERS:
        _quiet_logger(name, quiet=True)

    return _state["file_path"]


def read_recent_lines(lines: int = 300, contains: Optional[str] = None) -> List[str]:
    """Tail the persistent log without loading it whole.

    The active file is capped at ``LOG_FILE_MAX_BYTES``, so reading it entirely
    would still be bounded - but only the last ``lines`` are ever shown, and a
    rotating file can be replaced between the size check and the read. Read
    backwards from the end in one seek instead.
    """
    path = _state.get("file_path")
    if path is None or not Path(path).exists():
        return []
    try:
        size = Path(path).stat().st_size
        # A rotation can leave the active file shorter than the window asked for.
        want = min(max(lines, 1) * 400 + 4096, size)
        with open(path, "rb") as fh:
            fh.seek(size - want)
            chunk = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    kept = chunk.splitlines()[-max(1, min(lines, 2000)):]
    if contains:
        needle = contains.lower()
        kept = [line for line in kept if needle in line.lower()]
    return kept


#: ``%(asctime)s [%(levelname)s] %(name)s: %(message)s`` - the format installed
#: above, matched so the tail can be presented as rows instead of raw text.
_LINE_RE = re.compile(
    r"^(?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[(?P<level>[A-Z]+)\] "
    r"(?P<logger>[\w.]+): "
    r"(?P<message>.*)$"
)

_LEVEL_SEVERITY = {
    "CRITICAL": "critical",
    "ERROR": "error",
    "WARNING": "warning",
    "INFO": "info",
    "DEBUG": "info",
}


def read_recent_entries(
    lines: int = 300,
    contains: Optional[str] = None,
    severity: Optional[str] = None,
) -> List[dict]:
    """The persistent log as structured rows, newest last.

    A line that does not match the format (a traceback continuation, or a write
    from a third-party logger) is kept rather than dropped: losing the body of a
    traceback to a parser would hide exactly the thing the file exists for. Such
    a line is attributed to the previous entry so a multi-line traceback stays
    readable in order.
    """
    entries: List[dict] = []
    for raw in read_recent_lines(lines=lines):
        match = _LINE_RE.match(raw)
        if match:
            when = match.group("when")
            level = match.group("level")
            entries.append({
                "timestamp": datetime.strptime(when, "%Y-%m-%d %H:%M:%S,%f"),
                "level": level,
                "severity": _LEVEL_SEVERITY.get(level, "info"),
                "logger": match.group("logger"),
                "message": match.group("message"),
            })
        elif entries:
            entries[-1]["message"] += "\n" + raw
        else:
            entries.append({
                "timestamp": None,
                "level": "INFO",
                "severity": "info",
                "logger": "mikroman",
                "message": raw,
            })
    if severity:
        want = severity.lower()
        entries = [e for e in entries if e["severity"] == want]
    if contains:
        # Filtered after parsing, not on the raw lines: a traceback continuation
        # belongs to its entry, and dropping the unmatched body would hide the
        # part of a failure that is usually being searched for.
        needle = contains.lower()
        entries = [e for e in entries if needle in e["message"].lower() or needle in e["logger"].lower()]
    return entries
