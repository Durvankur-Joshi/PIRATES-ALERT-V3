from unittest.mock import patch
import pytest
from app.security.url_validation import URLValidationError, validate_url


def test_valid_public_urls() -> None:
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        assert validate_url("https://example.com/search?q=test") == "https://example.com/search?q=test"
        assert validate_url("http://example.org/listing") == "http://example.org/listing"


def test_rejects_localhost_hostnames() -> None:
    for url in (
        "http://localhost",
        "http://localhost:8000/api",
        "https://sub.localhost/test",
        "http://broadcasthost/test",
        "http://internal.corp/test",
        "http://service.local/test",
        "http://gateway.lan/test",
    ):
        with pytest.raises(URLValidationError):
            validate_url(url, resolve_dns=False)


def test_rejects_loopback_and_private_ipv4() -> None:
    forbidden_urls = (
        "http://127.0.0.1/",
        "http://127.0.0.2:8080/",
        "http://127.100.0.1/",
        "http://0.0.0.0/",
        "http://10.0.0.1/",
        "http://10.254.1.1:3000/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://192.168.0.1/",
        "http://192.168.1.100:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://255.255.255.255/",
    )
    for url in forbidden_urls:
        with pytest.raises(URLValidationError):
            validate_url(url, resolve_dns=False)


def test_rejects_ipv6_loopback_and_private() -> None:
    forbidden_ipv6 = (
        "http://[::1]/",
        "http://[::]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:192.168.1.1]/",
    )
    for url in forbidden_ipv6:
        with pytest.raises(URLValidationError):
            validate_url(url, resolve_dns=False)


def test_rejects_unsupported_schemes() -> None:
    for url in (
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<h1>Hello</h1>",
        "gopher://example.com/",
    ):
        with pytest.raises(URLValidationError):
            validate_url(url, resolve_dns=False)


def test_rejects_embedded_credentials() -> None:
    with pytest.raises(URLValidationError):
        validate_url("http://admin:secret@example.com/dashboard", resolve_dns=False)


def test_rejects_malformed_urls() -> None:
    invalid_inputs = ("", "   ", "not-a-url", "http://", "https://:8080")
    for item in invalid_inputs:
        with pytest.raises(URLValidationError):
            validate_url(item, resolve_dns=False)


def test_rejects_hostname_resolving_to_private_ip() -> None:
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with pytest.raises(URLValidationError):
            validate_url("http://malicious-public-domain.com/path", resolve_dns=True)

    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(2, 1, 6, "", ("192.168.1.50", 443))]
        with pytest.raises(URLValidationError):
            validate_url("https://another-domain.com/path", resolve_dns=True)


def test_rejects_unresolvable_hostname() -> None:
    import socket
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(URLValidationError):
            validate_url("https://non-existent-domain-xyz123.com", resolve_dns=True)
