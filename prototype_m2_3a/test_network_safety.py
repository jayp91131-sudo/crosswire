"""
Crosswire M2.3 Network Safety & SSRF Prevention Test Suite.
Validates exact supplier registry, loopback/private/link-local blocking,
IPv4-mapped IPv6, DNS rebinding pinning, and redirect credential stripping.
"""

import sys
import socket
from pathlib import Path

# Ensure .venv site-packages is accessible
VENV_SITE = Path(r"C:\Users\jayp9\.gemini\antigravity\scratch\crosswire\.venv\Lib\site-packages")
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))

import pytest
from safe_target_validator import (
    validate_target_url,
    validate_redirect,
    SafePinnedConnection,
    SSRFBlockedError,
    UnauthorizedTargetError,
    InsecureProtocolError
)

def mock_resolver(resolved_ips):
    """Factory creating a mock DNS resolver returning specific IPs."""
    def _resolve(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (ip, port)) for ip in resolved_ips]
    return _resolve


def test_01_exact_supplier_registry_authorized():
    """1. Valid whitelisted supplier hostname with public IP passes validation."""
    resolver = mock_resolver(["104.18.26.120"])
    ips = validate_target_url("https://api.expedia.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)
    assert ips == ["104.18.26.120"]


def test_02_unauthorized_supplier_host_rejected():
    """2. Hostname not in supplier registry whitelist is rejected."""
    resolver = mock_resolver(["104.18.26.120"])
    with pytest.raises(UnauthorizedTargetError, match="not authorized for supplier"):
        validate_target_url("https://attacker.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)


def test_03_insecure_http_scheme_rejected():
    """3. Non-HTTPS schemes are rejected."""
    with pytest.raises(InsecureProtocolError, match="Insecure scheme 'http'"):
        validate_target_url("http://api.expedia.com/v3/bookings", "SUP_EXPEDIA")


def test_04_loopback_ipv4_rejected():
    """4. Loopback IPv4 (127.0.0.1) resolution is rejected as SSRF."""
    resolver = mock_resolver(["127.0.0.1"])
    with pytest.raises(SSRFBlockedError, match="Loopback address blocked"):
        validate_target_url("https://api.expedia.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)


def test_05_loopback_ipv6_rejected():
    """5. Loopback IPv6 (::1) resolution is rejected as SSRF."""
    resolver = mock_resolver(["::1"])
    with pytest.raises(SSRFBlockedError, match="Loopback address blocked"):
        validate_target_url("https://api.expedia.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)


def test_06_private_rfc1918_ips_rejected():
    """6. RFC 1918 private IPv4 addresses (10.x, 172.16.x, 192.168.x) rejected."""
    for private_ip in ["10.0.0.1", "172.16.5.10", "192.168.1.1"]:
        resolver = mock_resolver([private_ip])
        with pytest.raises(SSRFBlockedError, match="Private network address blocked"):
            validate_target_url("https://api.expedia.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)


def test_07_link_local_and_cloud_metadata_rejected():
    """7. Link-local and AWS/GCP cloud metadata IP (169.254.169.254) rejected."""
    resolver = mock_resolver(["169.254.169.254"])
    with pytest.raises(SSRFBlockedError, match="Cloud metadata service IP blocked"):
        validate_target_url("https://api.expedia.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)


def test_08_ipv4_mapped_ipv6_loopback_rejected():
    """8. IPv4-mapped IPv6 loopback (::ffff:127.0.0.1) unwrapped and rejected."""
    resolver = mock_resolver(["::ffff:127.0.0.1"])
    with pytest.raises(SSRFBlockedError, match="Loopback address blocked"):
        validate_target_url("https://api.expedia.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)


def test_09_ipv4_mapped_ipv6_metadata_rejected():
    """9. IPv4-mapped IPv6 cloud metadata (::ffff:169.254.169.254) unwrapped and rejected."""
    resolver = mock_resolver(["::ffff:169.254.169.254"])
    with pytest.raises(SSRFBlockedError, match="Cloud metadata service IP blocked"):
        validate_target_url("https://api.expedia.com/v3/bookings", "SUP_EXPEDIA", dns_resolver=resolver)


def test_10_dns_rebinding_pinning():
    """10. SafePinnedConnection verifies pinned IP and detects DNS rebinding."""
    conn = SafePinnedConnection("api.expedia.com", "104.18.26.120")
    # Subsequent resolution returns original IP -> Valid
    assert conn.verify_rebind(["104.18.26.120"]) is True

    # DNS Rebind attack: subsequent resolution returns attacker or loopback IP -> Detected
    assert conn.verify_rebind(["127.0.0.1"]) is False
    assert conn.verify_rebind(["203.0.113.99"]) is False


def test_11_redirect_validation_blocks_unauthorized_host():
    """11. Redirect to non-whitelisted domain is blocked."""
    headers = {"Authorization": "Bearer secret_token_123", "X-API-Key": "key_abc"}
    resolver = mock_resolver(["104.18.26.120"])
    
    with pytest.raises(UnauthorizedTargetError, match="not authorized for supplier"):
        validate_redirect(
            "https://api.expedia.com/v3/bookings",
            "https://evil-spoof.com/steal",
            "SUP_EXPEDIA",
            headers,
            dns_resolver=resolver
        )


def test_12_redirect_strips_credentials_on_host_change():
    """12. Redirect across distinct whitelisted hosts strips sensitive auth headers."""
    headers = {
        "Authorization": "Bearer secret_token_123",
        "X-API-Key": "key_abc",
        "Cookie": "session=xyz",
        "Content-Type": "application/json"
    }
    resolver = mock_resolver(["104.18.26.120"])

    # Redirect from api.expedia.com to partner.api.expedia.com (both valid, but distinct hosts)
    url, safe_headers = validate_redirect(
        "https://api.expedia.com/v3/bookings",
        "https://partner.api.expedia.com/v3/bookings",
        "SUP_EXPEDIA",
        headers,
        dns_resolver=resolver
    )

    assert url == "https://partner.api.expedia.com/v3/bookings"
    assert "Authorization" not in safe_headers
    assert "X-API-Key" not in safe_headers
    assert "Cookie" not in safe_headers
    assert safe_headers["Content-Type"] == "application/json"


def run_all_network_tests():
    tests = [
        test_01_exact_supplier_registry_authorized,
        test_02_unauthorized_supplier_host_rejected,
        test_03_insecure_http_scheme_rejected,
        test_04_loopback_ipv4_rejected,
        test_05_loopback_ipv6_rejected,
        test_06_private_rfc1918_ips_rejected,
        test_07_link_local_and_cloud_metadata_rejected,
        test_08_ipv4_mapped_ipv6_loopback_rejected,
        test_09_ipv4_mapped_ipv6_metadata_rejected,
        test_10_dns_rebinding_pinning,
        test_11_redirect_validation_blocks_unauthorized_host,
        test_12_redirect_strips_credentials_on_host_change,
    ]
    print(f"Executing {len(tests)} network safety & SSRF prevention tests...")
    passed = 0
    for t in tests:
        t()
        print(f"  [PASS] {t.__name__}")
        passed += 1
    print(f"\nNetwork Safety Test Summary: {passed} passed, 0 failed out of {len(tests)} tests.")


if __name__ == "__main__":
    run_all_network_tests()
