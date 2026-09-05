"""Compact offline Geo-IP engine.

Provides microsecond in-memory resolution of IP addresses to ISO country codes,
country names, and flag emojis without external network calls or rate limits.
"""

import ipaddress
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger("mikroman.geoip")


@dataclass(frozen=True)
class GeoLocation:
    country_code: str
    country_name: str
    flag_emoji: str


# Mapping of ISO-3166-1 alpha-2 codes to English names
COUNTRY_NAMES: Dict[str, str] = {
    "LOCAL": "Local Network",
    "??": "Unknown",
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AG": "Antigua and Barbuda", "AI": "Anguilla", "AL": "Albania",
    "AM": "Armenia", "AO": "Angola", "AQ": "Antarctica",
    "AR": "Argentina", "AS": "American Samoa", "AT": "Austria",
    "AU": "Australia", "AW": "Aruba", "AX": "Åland Islands",
    "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina", "BB": "Barbados",
    "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso",
    "BG": "Bulgaria", "BH": "Bahrain", "BI": "Burundi",
    "BJ": "Benin", "BL": "Saint Barthélemy", "BM": "Bermuda",
    "BN": "Brunei", "BO": "Bolivia", "BQ": "Caribbean Netherlands",
    "BR": "Brazil", "BS": "Bahamas", "BT": "Bhutan",
    "BW": "Botswana", "BY": "Belarus", "BZ": "Belize",
    "CA": "Canada", "CC": "Cocos Islands", "CD": "Congo - Kinshasa",
    "CF": "Central African Republic", "CG": "Congo - Brazzaville", "CH": "Switzerland",
    "CI": "Côte d'Ivoire", "CK": "Cook Islands", "CL": "Chile",
    "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CV": "Cape Verde",
    "CW": "Curaçao", "CX": "Christmas Island", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DJ": "Djibouti",
    "DK": "Denmark", "DM": "Dominica", "DO": "Dominican Republic",
    "DZ": "Algeria", "EC": "Ecuador", "EE": "Estonia",
    "EG": "Egypt", "EH": "Western Sahara", "ER": "Eritrea",
    "ES": "Spain", "ET": "Ethiopia", "FI": "Finland",
    "FJ": "Fiji", "FK": "Falkland Islands", "FM": "Micronesia",
    "FO": "Faroe Islands", "FR": "France", "GA": "Gabon",
    "GB": "United Kingdom", "GD": "Grenada", "GE": "Georgia",
    "GF": "French Guiana", "GG": "Guernsey", "GH": "Ghana",
    "GI": "Gibraltar", "GL": "Greenland", "GM": "Gambia",
    "GN": "Guinea", "GP": "Guadeloupe", "GQ": "Equatorial Guinea",
    "GR": "Greece", "GT": "Guatemala", "GU": "Guam",
    "GW": "Guinea-Bissau", "GY": "Guyana", "HK": "Hong Kong",
    "HN": "Honduras", "HR": "Croatia", "HT": "Haiti",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland",
    "IL": "Israel", "IM": "Isle of Man", "IN": "India",
    "IO": "British Indian Ocean Territory", "IQ": "Iraq", "IR": "Iran",
    "IS": "Iceland", "IT": "Italy", "JE": "Jersey",
    "JM": "Jamaica", "JO": "Jordan", "JP": "Japan",
    "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia",
    "KI": "Kiribati", "KM": "Comoros", "KN": "Saint Kitts and Nevis",
    "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait",
    "KY": "Cayman Islands", "KZ": "Kazakhstan", "LA": "Laos",
    "LB": "Lebanon", "LC": "Saint Lucia", "LI": "Liechtenstein",
    "LK": "Sri Lanka", "LR": "Liberia", "LS": "Lesotho",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia",
    "LY": "Libya", "MA": "Morocco", "MC": "Monaco",
    "MD": "Moldova", "ME": "Montenegro", "MF": "Saint Martin",
    "MG": "Madagascar", "MH": "Marshall Islands", "MK": "North Macedonia",
    "ML": "Mali", "MM": "Myanmar", "MN": "Mongolia",
    "MO": "Macao", "MP": "Northern Mariana Islands", "MQ": "Martinique",
    "MR": "Mauritania", "MS": "Montserrat", "MT": "Malta",
    "MU": "Mauritius", "MV": "Maldives", "MW": "Malawi",
    "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique",
    "NA": "Namibia", "NC": "New Caledonia", "NE": "Niger",
    "NF": "Norfolk Island", "NG": "Nigeria", "NI": "Nicaragua",
    "NL": "Netherlands", "NO": "Norway", "NP": "Nepal",
    "NR": "Nauru", "NU": "Niue", "NZ": "New Zealand",
    "OM": "Oman", "PA": "Panama", "PE": "Peru",
    "PF": "French Polynesia", "PG": "Papua New Guinea", "PH": "Philippines",
    "PK": "Pakistan", "PL": "Poland", "PM": "Saint Pierre and Miquelon",
    "PN": "Pitcairn Islands", "PR": "Puerto Rico", "PS": "Palestine",
    "PT": "Portugal", "PW": "Palau", "PY": "Paraguay",
    "QA": "Qatar", "RE": "Réunion", "RO": "Romania",
    "RS": "Serbia", "RU": "Russia", "RW": "Rwanda",
    "SA": "Saudi Arabia", "SB": "Solomon Islands", "SC": "Seychelles",
    "SD": "Sudan", "SE": "Sweden", "SG": "Singapore",
    "SH": "Saint Helena", "SI": "Slovenia", "SJ": "Svalbard and Jan Mayen",
    "SK": "Slovakia", "SL": "Sierra Leone", "SM": "San Marino",
    "SN": "Senegal", "SO": "Somalia", "SR": "Suriname",
    "SS": "South Sudan", "ST": "São Tomé and Príncipe", "SV": "El Salvador",
    "SX": "Sint Maarten", "SY": "Syria", "SZ": "Eswatini",
    "TC": "Turks and Caicos Islands", "TD": "Chad", "TF": "French Southern Territories",
    "TG": "Togo", "TH": "Thailand", "TJ": "Tajikistan",
    "TK": "Tokelau", "TL": "Timor-Leste", "TM": "Turkmenistan",
    "TN": "Tunisia", "TO": "Tonga", "TR": "Turkey",
    "TT": "Trinidad and Tobago", "TV": "Tuvalu", "TW": "Taiwan",
    "TZ": "Tanzania", "UA": "Ukraine", "UG": "Uganda",
    "UM": "U.S. Outlying Islands", "US": "United States", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VA": "Vatican City", "VC": "Saint Vincent and the Grenadines",
    "VE": "Venezuela", "VG": "British Virgin Islands", "VI": "U.S. Virgin Islands",
    "VN": "Vietnam", "VU": "Vanuatu", "WF": "Wallis and Futuna",
    "WS": "Samoa", "YE": "Yemen", "YT": "Mayotte",
    "ZA": "South Africa", "ZM": "Zambia", "ZW": "Zimbabwe",
}


def country_code_to_flag(country_code: str) -> str:
    """Convert 2-letter ISO country code to Unicode flag emoji."""
    if not country_code or country_code == "LOCAL":
        return "🏠"
    if country_code == "??":
        return "🌐"
    clean = country_code.upper()
    if len(clean) != 2 or not clean.isalpha():
        return "🌐"
    return "".join(chr(127397 + ord(char)) for char in clean)


# Built-in prefix table.
#
# Deliberately small. Only ranges whose country is actually known are listed:
# anycast resolvers, CDN edges announced from a single country, and the local
# Uzbek carriers this deployment talks to daily. RIR-wide /8s (91/8, 80/8,
# 217/8 ...) are NOT listed - RIPE allocates those across fifty countries, so
# labelling them "RU" or "GB" would be a guess printed as a fact.
#
# Drop a MaxMind GeoLite2-Country.mmdb into ``data/`` (or point
# ``MIKROMAN_GEOIP_DB`` at one) to get real per-prefix accuracy; anything this
# table does not cover resolves to "Unknown" rather than to an invented answer.
_BUILTIN_PREFIXES: List[Tuple[ipaddress.IPv4Network, str]] = [
    # Anycast resolvers
    (ipaddress.IPv4Network("1.1.1.0/24"), "US"),      # Cloudflare DNS
    (ipaddress.IPv4Network("1.0.0.0/24"), "US"),      # Cloudflare DNS
    (ipaddress.IPv4Network("8.8.8.0/24"), "US"),      # Google DNS
    (ipaddress.IPv4Network("8.8.4.0/24"), "US"),      # Google DNS
    (ipaddress.IPv4Network("9.9.9.0/24"), "US"),      # Quad9
    (ipaddress.IPv4Network("208.67.222.0/24"), "US"), # OpenDNS
    (ipaddress.IPv4Network("77.88.8.0/24"), "RU"),    # Yandex DNS
    # CDN / cloud edges
    (ipaddress.IPv4Network("142.250.0.0/15"), "US"),  # Google
    (ipaddress.IPv4Network("172.217.0.0/16"), "US"),  # Google
    (ipaddress.IPv4Network("64.233.160.0/19"), "US"), # Google (legacy web/mail range, still in daily use)
    (ipaddress.IPv4Network("104.16.0.0/12"), "US"),   # Cloudflare
    (ipaddress.IPv4Network("162.158.0.0/15"), "US"),  # Cloudflare
    (ipaddress.IPv4Network("151.101.0.0/16"), "US"),  # Fastly
    (ipaddress.IPv4Network("199.232.0.0/16"), "US"),  # Fastly
    (ipaddress.IPv4Network("77.88.0.0/18"), "RU"),    # Yandex
    (ipaddress.IPv4Network("87.250.250.0/24"), "RU"), # Yandex
    (ipaddress.IPv4Network("149.154.160.0/20"), "GB"),# Telegram Messenger Inc (AS62041/AS44907)
    # Local carriers
    (ipaddress.IPv4Network("213.230.64.0/18"), "UZ"), # Ucell
    (ipaddress.IPv4Network("84.54.64.0/18"), "UZ"),   # Uztelecom
]

_mmdb_reader = None
_mmdb_checked = False


def _mmdb_candidate_paths() -> List[str]:
    """Places a GeoLite2 database may live, most specific first.

    The process CWD differs between `uvicorn` in the container (``/app``) and a
    local `pytest` run, so an unqualified ``data/`` path resolved to nothing in
    one of them. ``MIKROMAN_GEOIP_DB`` wins when set; otherwise the app's own
    data directory is tried before the CWD-relative path.
    """
    explicit = os.environ.get("MIKROMAN_GEOIP_DB")
    if explicit:
        return [explicit]
    repo_root = Path(__file__).resolve().parents[3]
    return [
        str(repo_root / "data" / "GeoLite2-Country.mmdb"),
        os.path.join("/data", "GeoLite2-Country.mmdb"),
        os.path.join("data", "GeoLite2-Country.mmdb"),
    ]


def _get_mmdb_reader():
    global _mmdb_reader, _mmdb_checked
    if not _mmdb_checked:
        _mmdb_checked = True
        for db_path in _mmdb_candidate_paths():
            if not os.path.exists(db_path):
                continue
            try:
                import maxminddb
                _mmdb_reader = maxminddb.open_database(db_path)
                logger.info(f"GeoIP: using MaxMind database at {db_path}")
                break
            except Exception as e:
                logger.warning(f"GeoIP: could not open {db_path}: {e}")
    return _mmdb_reader


def strip_port(addr: str) -> str:
    """Strip a trailing ``:port`` from an address without destroying IPv6.

    ``"1.2.3.4:443"`` -> ``"1.2.3.4"``, ``"[2001:db8::1]:443"`` -> ``"2001:db8::1"``,
    and a bare ``"2001:db8::1"`` is returned untouched. Splitting on the first
    colon - as this did originally - turned every IPv6 address into the garbage
    ``"2001"``, which failed to parse and so reported every IPv6 peer as being
    on the local network.
    """
    raw = str(addr).strip()
    if not raw:
        return ""
    if raw.startswith("["):
        # Bracketed IPv6, with or without a port
        end = raw.find("]")
        if end != -1:
            return raw[1:end]
        return raw.lstrip("[")
    if raw.count(":") == 1:
        # Exactly one colon: IPv4 with a port (IPv6 always has two or more)
        host, _, port = raw.partition(":")
        return host if port.isdigit() else raw
    return raw


def resolve_ip_location(ip: str) -> GeoLocation:
    """Resolve an IP address to country information.

    Runs entirely in memory with zero external I/O. An address the engine cannot
    place resolves to ``"??"`` / "Unknown" - never to a guessed country.
    """
    clean_ip = strip_port(str(ip).strip().split("/")[0])
    try:
        ip_obj = ipaddress.ip_address(clean_ip)
    except ValueError:
        return GeoLocation(country_code="??", country_name="Unknown", flag_emoji="🌐")

    # Short-circuit private, loopback, multicast, or link-local
    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return GeoLocation(country_code="LOCAL", country_name="Local Network", flag_emoji="🏠")

    # Check MMDB reader if available
    reader = _get_mmdb_reader()
    if reader is not None:
        try:
            match = reader.get(str(ip_obj))
            if match and "country" in match:
                iso = match["country"].get("iso_code")
                if iso:
                    name = match["country"].get("names", {}).get("en") or COUNTRY_NAMES.get(iso, iso)
                    return GeoLocation(country_code=iso, country_name=name, flag_emoji=country_code_to_flag(iso))
        except Exception:
            pass

    # Built-in fallback table lookup
    if isinstance(ip_obj, ipaddress.IPv4Address):
        for network, code in _BUILTIN_PREFIXES:
            if ip_obj in network:
                name = COUNTRY_NAMES.get(code, code)
                return GeoLocation(country_code=code, country_name=name, flag_emoji=country_code_to_flag(code))

    # Nothing placed it. Say so, rather than inferring a country from the first
    # octet: IANA stopped handing out /8s per region in 2011, so that inference
    # produced a confident, wrong flag on most of the public internet.
    return GeoLocation(country_code="??", country_name="Unknown", flag_emoji="🌐")
