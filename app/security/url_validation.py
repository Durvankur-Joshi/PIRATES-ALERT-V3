import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

RESTRICTED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("100::/64"),
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]

FORBIDDEN_HOSTNAME_SUFFIXES = (
    "localhost",
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".arpa",
    "metadata.google.internal",
    "instance-data",
    "metadata.azure.com",
)


class URLValidationError(ValueError):
    pass


def is_ip_address_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Security requirement: Extract embedded IPv4 from IPv4-mapped IPv6 to prevent SSRF bypass
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True

    for network in RESTRICTED_NETWORKS:
        if ip in network:
            return True

    return False


def validate_ip_string(ip_str: str) -> None:
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError as e:
        raise URLValidationError(f"Invalid IP address representation: {ip_str}") from e

    if is_ip_address_forbidden(ip_obj):
        raise URLValidationError(f"Target IP address is restricted or non-public: {ip_str}")


def resolve_hostname_ips(hostname: str, port: int) -> list[str]:
    try:
        addr_info = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise URLValidationError(f"Hostname could not be resolved: {hostname}") from e

    ip_addresses: list[str] = []
    for entry in addr_info:
        sockaddr = entry[4]
        ip = sockaddr[0]
        if ip not in ip_addresses:
            ip_addresses.append(ip)

    if not ip_addresses:
        raise URLValidationError(f"No IP addresses resolved for hostname: {hostname}")

    return ip_addresses


def validate_url(url: str, resolve_dns: bool = True) -> str:
    if not isinstance(url, str):
        raise URLValidationError("URL must be a string")

    clean_url = url.strip()
    if not clean_url:
        raise URLValidationError("URL cannot be empty")

    try:
        parsed = urlsplit(clean_url)
    except Exception as e:
        raise URLValidationError(f"Malformed URL: {e}") from e

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise URLValidationError(f"Unsupported URL scheme '{scheme}'. Only HTTP and HTTPS are permitted.")

    # Security requirement: Reject credentials embedded in authority to prevent credential leaking and URL confusion
    if parsed.username or parsed.password:
        raise URLValidationError("Embedded credentials in URLs are strictly forbidden")

    hostname = parsed.hostname
    if not hostname:
        raise URLValidationError("URL is missing a valid hostname")

    hostname_lower = hostname.lower()

    if hostname_lower in ("localhost", "broadcasthost") or any(
        hostname_lower.endswith(suffix) for suffix in FORBIDDEN_HOSTNAME_SUFFIXES
    ):
        raise URLValidationError(f"Forbidden private or local hostname: {hostname}")

    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    elif not (1 <= port <= 65535):
        raise URLValidationError(f"Invalid port number: {port}")

    # Security requirement: Strip bracket notation on IPv6 literals prior to ip_address validation
    host_to_check = hostname_lower
    if host_to_check.startswith("[") and host_to_check.endswith("]"):
        host_to_check = host_to_check[1:-1]

    try:
        if host_to_check.isdigit():
            ip_obj = ipaddress.IPv4Address(int(host_to_check))
            if is_ip_address_forbidden(ip_obj):
                raise URLValidationError(f"Target IP address is restricted or non-public: {host_to_check}")
            is_ip_literal = True
        else:
            ipaddress.ip_address(host_to_check)
            is_ip_literal = True
    except ValueError:
        is_ip_literal = False

    if is_ip_literal and not host_to_check.isdigit():
        validate_ip_string(host_to_check)
    elif not is_ip_literal and resolve_dns:
        resolved_ips = resolve_hostname_ips(hostname_lower, port)
        for ip in resolved_ips:
            validate_ip_string(ip)

    normalized_url = urlunsplit(
        (scheme, parsed.netloc, parsed.path or "/", parsed.query, parsed.fragment)
    )
    return normalized_url
