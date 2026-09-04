import hashlib
import re

VOLATILE_HEADER_RE = re.compile(
    r"^#\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+by\s+RouterOS\b.*$",
    re.MULTILINE,
)


def normalize_rsc(rsc_text: str) -> str:
    """Normalize RouterOS .rsc script export by removing volatile timestamp headers

    and standardizing newlines and trailing whitespace.
    """
    if not rsc_text:
        return ""
    # Standardize line endings
    text = rsc_text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip volatile RouterOS timestamp line
    text = VOLATILE_HEADER_RE.sub("", text)
    # Strip trailing whitespaces per line
    lines = [line.rstrip() for line in text.split("\n")]
    # Remove leading blank lines
    while lines and not lines[0]:
        lines.pop(0)
    # Remove trailing blank lines
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def compute_fingerprint(rsc_text: str) -> str:
    """Return SHA-256 hex digest of normalized RouterOS configuration script."""
    normalized = normalize_rsc(rsc_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
