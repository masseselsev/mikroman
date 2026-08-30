"""Human-readable formatting shared by alerts and bot messages."""


def format_bytes_human(num_bytes: int) -> str:
    """Render a byte count the way the dashboard does, for alert parity."""
    value = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} TB"
