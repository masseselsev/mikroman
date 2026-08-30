import logging
import re
from typing import Dict, Optional

import httpx

logger = logging.getLogger("mikroman.vendor_lookup")

# Common manufacturer name cleaner
VENDOR_ALIASES = [
    (re.compile(r"Apple.*", re.IGNORECASE), "Apple"),
    (re.compile(r"Intel.*", re.IGNORECASE), "Intel"),
    (re.compile(r"Samsung.*", re.IGNORECASE), "Samsung"),
    (re.compile(r"Xiaomi.*|Beijing Xiaomi.*", re.IGNORECASE), "Xiaomi"),
    (re.compile(r"Huawei.*", re.IGNORECASE), "Huawei"),
    (re.compile(r"Sony.*", re.IGNORECASE), "Sony"),
    (re.compile(r"LG Electron.*", re.IGNORECASE), "LG"),
    (re.compile(r"Google.*", re.IGNORECASE), "Google"),
    (re.compile(r"Microsoft.*", re.IGNORECASE), "Microsoft"),
    (re.compile(r"Raspberry Pi.*", re.IGNORECASE), "Raspberry Pi"),
    (re.compile(r"Espressif.*", re.IGNORECASE), "Espressif (IoT)"),
    (re.compile(r"MikroTik.*|Routerboard.*", re.IGNORECASE), "MikroTik"),
    (re.compile(r"TP-Link.*", re.IGNORECASE), "TP-Link"),
    (re.compile(r"Ubiquiti.*", re.IGNORECASE), "Ubiquiti"),
    (re.compile(r"Realtek.*", re.IGNORECASE), "Realtek"),
    (re.compile(r"Hon Hai.*|Foxconn.*", re.IGNORECASE), "Foxconn"),
    (re.compile(r"ASUSTeK.*|ASUS.*", re.IGNORECASE), "ASUS"),
    (re.compile(r"Dell.*", re.IGNORECASE), "Dell"),
    (re.compile(r"HP.*|Hewlett.*", re.IGNORECASE), "HP"),
    (re.compile(r"Lenovo.*", re.IGNORECASE), "Lenovo"),
    (re.compile(r"Amazon.*", re.IGNORECASE), "Amazon"),
    (re.compile(r"Cisco.*", re.IGNORECASE), "Cisco"),
    (re.compile(r"OnePlus.*", re.IGNORECASE), "OnePlus"),
    (re.compile(r"Tuya.*", re.IGNORECASE), "Tuya (Smart Home)"),
    (re.compile(r"Sonoff.*|Itead.*", re.IGNORECASE), "Sonoff (IoT)"),
    (re.compile(r"zte.*", re.IGNORECASE), "ZTE"),
    (re.compile(r"Quanta.*", re.IGNORECASE), "Quanta Computer"),
]

# Fast built-in offline OUI dictionary for instantaneous offline lookups
BUILTIN_OUIS: Dict[str, str] = {
    # Apple
    "AC:DE:48": "Apple", "F0:18:98": "Apple", "DC:A9:04": "Apple", "3C:22:FB": "Apple",
    "F4:34:F0": "Apple", "BC:D1:1F": "Apple", "A8:66:7F": "Apple", "CC:2D:21": "Apple",
    "F0:99:B6": "Apple", "8C:85:90": "Apple", "38:CA:DA": "Apple", "70:EC:E4": "Apple",
    "9C:20:7B": "Apple", "A4:C3:61": "Apple", "B8:78:2E": "Apple", "E0:B9:E5": "Apple",
    "88:66:5A": "Apple", "F4:0F:24": "Apple", "14:7D:DA": "Apple", "28:CF:E9": "Apple",

    # Intel
    "FC:6D:77": "Intel", "38:F9:D3": "Intel", "00:15:00": "Intel", "48:51:B7": "Intel",
    "00:1B:21": "Intel", "00:1E:67": "Intel", "00:21:5C": "Intel", "00:22:FA": "Intel",
    "00:23:14": "Intel", "00:24:D7": "Intel", "00:26:C6": "Intel", "00:27:10": "Intel",
    "34:13:E8": "Intel", "80:86:F2": "Intel", "A4:4C:C8": "Intel", "C8:5B:76": "Intel",

    # Samsung
    "50:EC:50": "Samsung", "8C:77:12": "Samsung", "D0:03:DF": "Samsung", "98:52:B1": "Samsung",
    "00:07:AB": "Samsung", "00:12:47": "Samsung", "00:15:B9": "Samsung", "00:17:C9": "Samsung",
    "08:EE:8B": "Samsung", "18:67:B0": "Samsung", "24:F5:AA": "Samsung", "34:23:BA": "Samsung",
    "40:40:A7": "Samsung", "5C:A3:9D": "Samsung", "78:47:1D": "Samsung", "94:65:2D": "Samsung",

    # Xiaomi
    "64:64:4A": "Xiaomi", "7C:49:EB": "Xiaomi", "04:CF:8C": "Xiaomi", "AC:C1:EE": "Xiaomi",
    "18:59:36": "Xiaomi", "28:6C:07": "Xiaomi", "34:CE:00": "Xiaomi", "50:8F:4C": "Xiaomi",
    "58:44:98": "Xiaomi", "78:11:DC": "Xiaomi", "8C:BE:BE": "Xiaomi", "D4:97:0B": "Xiaomi",

    # Espressif (ESP8266 / ESP32 IoT)
    "48:A9:D2": "Espressif (IoT)", "24:6F:28": "Espressif (IoT)", "30:AE:A4": "Espressif (IoT)",
    "84:F3:EB": "Espressif (IoT)", "A4:CF:12": "Espressif (IoT)", "BC:DD:C2": "Espressif (IoT)",
    "EC:FA:BC": "Espressif (IoT)", "24:0A:C4": "Espressif (IoT)", "3C:61:05": "Espressif (IoT)",
    "58:BF:25": "Espressif (IoT)", "68:C6:3A": "Espressif (IoT)", "7C:DF:A1": "Espressif (IoT)",

    # Raspberry Pi
    "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    "28:CD:C1": "Raspberry Pi", "D8:3A:DD": "Raspberry Pi",

    # MikroTik
    "00:0C:42": "MikroTik", "48:8F:5A": "MikroTik", "64:D1:54": "MikroTik", "74:4D:28": "MikroTik",
    "B8:69:F4": "MikroTik", "C4:AD:34": "MikroTik", "D4:CA:6D": "MikroTik", "E4:8D:8C": "MikroTik",
    "18:FD:74": "MikroTik", "2C:C8:1B": "MikroTik", "4C:5E:0C": "MikroTik", "78:9A:18": "MikroTik",

    # Realtek
    "00:E0:4C": "Realtek", "52:54:4C": "Realtek", "00:0B:2F": "Realtek", "40:8D:5C": "Realtek",

    # TP-Link
    "00:27:19": "TP-Link", "14:CC:20": "TP-Link", "50:C7:BF": "TP-Link", "70:4F:57": "TP-Link",
    "98:48:27": "TP-Link", "B0:95:75": "TP-Link", "C0:06:C3": "TP-Link", "D8:0D:17": "TP-Link",

    # Ubiquiti
    "00:27:22": "Ubiquiti", "04:18:D6": "Ubiquiti", "24:A4:3C": "Ubiquiti", "68:D7:9A": "Ubiquiti",
    "78:8A:20": "Ubiquiti", "80:2A:A8": "Ubiquiti", "B4:FB:E4": "Ubiquiti", "DC:9F:DB": "Ubiquiti",
    "FC:EC:DA": "Ubiquiti",

    # Sony / PlayStation
    "00:1A:7D": "Sony", "FC:0F:E6": "Sony", "70:9E:29": "Sony Interactive (PlayStation)",
    "00:04:1F": "Sony", "00:13:15": "Sony", "00:19:C5": "Sony", "F8:46:1C": "Sony",

    # Google / Nest
    "60:45:BD": "Google", "54:60:09": "Google", "D8:6C:63": "Google", "F4:F5:DB": "Google",
    "00:1A:11": "Google", "3C:5A:B4": "Google", "48:D6:D5": "Google", "94:EB:CD": "Google",

    # Amazon
    "00:FC:8B": "Amazon", "38:F7:3D": "Amazon", "44:65:0D": "Amazon", "50:DC:E7": "Amazon",
    "68:37:E9": "Amazon", "74:75:48": "Amazon", "84:D6:D0": "Amazon", "AC:63:BE": "Amazon",

    # Virtualization
    "00:05:69": "VMware", "00:0C:29": "VMware", "00:50:56": "VMware",
    "52:54:00": "QEMU/KVM", "08:00:27": "VirtualBox"
}


class VendorLookupService:
    """Provides high-performance offline + cached online MAC vendor resolution."""

    def __init__(self):
        self._cache: Dict[str, str] = dict(BUILTIN_OUIS)

    def _clean_vendor_name(self, raw_name: str) -> str:
        """Format and beautify manufacturer names."""
        clean = raw_name.strip()
        for pattern, replacement in VENDOR_ALIASES:
            if pattern.search(clean):
                return replacement
        # Trim company postfixes
        clean = re.sub(r",?\s*(Inc\.?|Co\.?|Ltd\.?|Corporation|GmbH|LLC|Technology|Technologies)\b.*$", "", clean, flags=re.IGNORECASE)
        return clean.strip() or raw_name.strip()

    def is_randomized_mac(self, mac: str) -> bool:
        """Check if MAC has locally administered (randomized / private) bit set."""
        if not mac or len(mac) < 2:
            return False
        try:
            first_byte = int(mac[:2], 16)
            return (first_byte & 0x02) != 0
        except ValueError:
            return False

    def lookup_sync(self, mac: str, hostname: Optional[str] = None) -> str:
        """Synchronous offline lookup from local OUI table & in-memory cache."""
        if not mac:
            return "Unknown Vendor"
        clean_mac = mac.upper().replace("-", ":")
        prefix = clean_mac[:8]

        # A randomized MAC is not an OUI: its first three bytes identify no
        # vendor, so a cached value under that prefix would be meaningless and
        # would shadow the hostname-derived identity resolved below.
        if prefix in self._cache and not self.is_randomized_mac(clean_mac):
            return self._cache[prefix]

        if self.is_randomized_mac(clean_mac):
            if hostname:
                h = hostname.lower()
                if any(x in h for x in ["iphone", "ipad", "macbook", "apple"]):
                    return "Apple (Private MAC)"
                if any(x in h for x in ["pixel", "google"]):
                    return "Google Pixel (Private MAC)"
                if any(x in h for x in ["galaxy", "samsung"]):
                    return "Samsung (Private MAC)"
                if any(x in h for x in ["xiaomi", "redmi"]):
                    return "Xiaomi (Private MAC)"
            return "Private MAC (Randomized)"

        return "Unknown Vendor"

    async def lookup_async(self, mac: str, hostname: Optional[str] = None) -> str:
        """
        Asynchronously resolve MAC vendor:
        1. Fast local OUI cache check (0ms).
        2. Detect randomized/private Wi-Fi MACs (iOS, Android, Windows).
        3. Fallback to free public MAC vendor API (maclookup.app / macvendors.com) with in-memory caching.
        """
        if not mac:
            return "Unknown Vendor"

        clean_mac = mac.upper().replace("-", ":")
        prefix = clean_mac[:8]

        # 1. Local cache hit. Skipped for randomized MACs: their first three
        # bytes identify no vendor, so a cached entry there is meaningless and
        # would shadow the hostname-derived identity resolved below.
        if prefix in self._cache and not self.is_randomized_mac(clean_mac):
            return self._cache[prefix]

        # 2. Check if MAC is randomized / private
        if self.is_randomized_mac(clean_mac):
            if hostname:
                h = hostname.lower()
                if any(x in h for x in ["iphone", "ipad", "macbook", "apple"]):
                    return "Apple (Private MAC)"
                if any(x in h for x in ["pixel", "google"]):
                    return "Google Pixel (Private MAC)"
                if any(x in h for x in ["galaxy", "samsung"]):
                    return "Samsung (Private MAC)"
                if any(x in h for x in ["xiaomi", "redmi"]):
                    return "Xiaomi (Private MAC)"
            return "Private MAC (Randomized)"

        # 3. Online API lookup
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                # Try maclookup.app v2 API (free JSON endpoint)
                resp = await client.get(f"https://api.maclookup.app/v2/macs/{clean_mac}")
                if resp.status_code == 200:
                    data = resp.json()
                    company = data.get("company")
                    if company and data.get("found"):
                        cleaned = self._clean_vendor_name(company)
                        self._cache[prefix] = cleaned
                        return cleaned

                # Fallback to macvendors.com
                resp2 = await client.get(f"https://api.macvendors.com/{clean_mac}")
                if resp2.status_code == 200 and resp2.text:
                    cleaned = self._clean_vendor_name(resp2.text)
                    self._cache[prefix] = cleaned
                    return cleaned
        except Exception as e:
            logger.debug(f"Online MAC vendor lookup failed for {clean_mac}: {e}")

        return "Unknown Vendor"


vendor_service = VendorLookupService()
