"""
Crosswire M2.3 Network Safety & SSRF Target Validator.
Enforces strict host whitelist registry, loopback/private/link-local IP blocking,
IPv4-mapped IPv6 normalization, DNS rebinding pinning, and redirect/credential security.
"""

import ipaddress
import socket
import urllib.parse
from typing import Dict, List, Optional, Set, Tuple

# Supplier Registry Whitelist (Strict mapping of supplier_id -> authorized domains)
SUPPLIER_REGISTRY_WHITELIST: Dict[str, Set[str]] = {
    "SUP_EXPEDIA": {"api.expedia.com", "partner.api.expedia.com"},
    "SUP_HOTELBEDS": {"api.hotelbeds.com", "secure.hotelbeds.com"},
    "SUP_BOOKING": {"distribution-xml.booking.com", "supply-api.booking.com"},
    "PAY_STRIPE": {"api.stripe.com"}
}

class NetworkSecurityError(Exception):
    """Base exception for all network safety and SSRF prevention violations."""
    pass

class SSRFBlockedError(NetworkSecurityError):
    """Raised when an IP target falls into a private, loopback, or disallowed range."""
    pass

class UnauthorizedTargetError(NetworkSecurityError):
    """Raised when target host is not in the authorized supplier registry."""
    pass

class InsecureProtocolError(NetworkSecurityError):
    """Raised when URL scheme is not HTTPS."""
    pass

class CredentialLeakageError(NetworkSecurityError):
    """Raised when sensitive credentials would be forwarded to an unauthorized origin."""
    pass


def is_ip_disallowed(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> Tuple[bool, str]:
    """
    Evaluates whether an IP address is disallowed (loopback, private, link-local, multicast, etc.).
    Handles IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1) by unwrapping them.
    """
    # 1. Check for IPv4-mapped IPv6
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        ip_obj = ip_obj.ipv4_mapped

    # 2. Check cloud metadata service (169.254.169.254)
    if str(ip_obj) == "169.254.169.254":
        return True, f"Cloud metadata service IP blocked: {ip_obj}"

    # 3. Check standard disallowed ranges
    if ip_obj.is_loopback:
        return True, f"Loopback address blocked: {ip_obj}"
    if ip_obj.is_link_local:
        return True, f"Link-local address blocked: {ip_obj}"
    if ip_obj.is_private:
        return True, f"Private network address blocked: {ip_obj}"
    if ip_obj.is_multicast:
        return True, f"Multicast address blocked: {ip_obj}"
    if ip_obj.is_reserved:
        return True, f"Reserved address blocked: {ip_obj}"
    if ip_obj.is_unspecified:
        return True, f"Unspecified (0.0.0.0 / ::) address blocked: {ip_obj}"

    return False, ""


def validate_target_url(
    url: str, 
    supplier_id: str, 
    dns_resolver=socket.getaddrinfo
) -> List[str]:
    """
    Validates a target URL against supplier registry, scheme, and IP restrictions.
    Returns the list of validated public IP addresses.
    """
    parsed = urllib.parse.urlparse(url)

    # 1. Strict HTTPS Scheme
    if parsed.scheme.lower() != "https":
        raise InsecureProtocolError(f"Insecure scheme '{parsed.scheme}'. Only 'https' is permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise UnauthorizedTargetError("Missing hostname in target URL.")

    # 2. Supplier Registry Whitelist Check
    allowed_hosts = SUPPLIER_REGISTRY_WHITELIST.get(supplier_id)
    if not allowed_hosts or hostname.lower() not in allowed_hosts:
        raise UnauthorizedTargetError(
            f"Host '{hostname}' is not authorized for supplier '{supplier_id}'. "
            f"Authorized hosts: {allowed_hosts}"
        )

    # 3. Prevent IP literals in URL host
    try:
        ip_literal = ipaddress.ip_address(hostname)
        disallowed, reason = is_ip_disallowed(ip_literal)
        if disallowed:
            raise SSRFBlockedError(reason)
        raise UnauthorizedTargetError("IP literals are not permitted as hostnames in supplier endpoints.")
    except ValueError:
        pass  # Hostname is a domain name, proceed to DNS resolution

    # 4. Resolve DNS and inspect ALL returned IP addresses
    try:
        addr_info = dns_resolver(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise NetworkSecurityError(f"DNS resolution failure for host '{hostname}': {e}")

    validated_ips: List[str] = []
    for entry in addr_info:
        sockaddr = entry[4]
        raw_ip = sockaddr[0]
        ip_obj = ipaddress.ip_address(raw_ip)
        
        disallowed, reason = is_ip_disallowed(ip_obj)
        if disallowed:
            raise SSRFBlockedError(f"Host '{hostname}' resolved to disallowed IP {raw_ip}: {reason}")
        
        if raw_ip not in validated_ips:
            validated_ips.append(raw_ip)

    if not validated_ips:
        raise NetworkSecurityError(f"No valid IP addresses resolved for host '{hostname}'.")

    return validated_ips


class SafePinnedConnection:
    """
    Represents a connection where the resolved IP is pinned to prevent DNS rebinding.
    """
    def __init__(self, hostname: str, pinned_ip: str):
        self.hostname = hostname
        self.pinned_ip = pinned_ip

    def verify_rebind(self, current_resolver_ips: List[str]) -> bool:
        """
        Ensures the hostname still resolves to the pinned IP. If the resolved IP changed
        to a private IP or divergent address, DNS rebinding is detected.
        """
        if self.pinned_ip not in current_resolver_ips:
            return False
        return True


def validate_redirect(
    original_url: str,
    redirect_url: str,
    supplier_id: str,
    headers: Dict[str, str],
    dns_resolver=socket.getaddrinfo
) -> Tuple[str, Dict[str, str]]:
    """
    Validates a redirect URL, ensuring it complies with HTTPS, supplier registry,
    and IP safety. Strips authentication headers if redirect transitions between hosts.
    """
    orig_parsed = urllib.parse.urlparse(original_url)
    redir_parsed = urllib.parse.urlparse(redirect_url)

    # Re-validate target URL
    validate_target_url(redirect_url, supplier_id, dns_resolver=dns_resolver)

    sanitized_headers = dict(headers)
    
    # If host changes, strip credentials
    if orig_parsed.hostname.lower() != redir_parsed.hostname.lower():
        sensitive_headers = {"authorization", "x-api-key", "cookie", "proxy-authorization"}
        for h in list(sanitized_headers.keys()):
            if h.lower() in sensitive_headers:
                del sanitized_headers[h]

    return redirect_url, sanitized_headers
