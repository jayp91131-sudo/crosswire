"""
Crosswire M2.3B Pinned Secure HTTP Transport.
Implements socket-level IP pinning, strict IP range filtering (including CGNAT),
port restrictions, userinfo rejection, SNI preservation, and redirect credential stripping.
"""

import ipaddress
import socket
import ssl
import urllib.parse
import http.client
from typing import Dict, List, Optional, Set, Tuple, Any

# Supplier Registry Whitelist
SUPPLIER_REGISTRY_WHITELIST: Dict[str, Set[str]] = {
    "SUP_EXPEDIA": {"api.expedia.com", "partner.api.expedia.com"},
    "SUP_HOTELBEDS": {"api.hotelbeds.com", "secure.hotelbeds.com"},
    "SUP_BOOKING": {"distribution-xml.booking.com", "supply-api.booking.com"},
    "PAY_STRIPE": {"api.stripe.com"},
    "TEST_SUPPLIER": {"localhost", "127.0.0.1", "test.supplier.local"}
}

# Approved Default HTTPS Ports
DEFAULT_APPROVED_PORTS: Set[int] = {443}

# CGNAT Network Range (RFC 6598)
CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

class TransportSecurityError(Exception):
    """Base exception for all pinned transport security violations."""
    pass

class SSRFBlockedError(TransportSecurityError):
    """Raised when an IP target falls into a private, loopback, CGNAT, or disallowed range."""
    pass

class UnauthorizedTargetError(TransportSecurityError):
    """Raised when target host is not in the authorized supplier registry."""
    pass

class InsecureProtocolError(TransportSecurityError):
    """Raised when URL scheme is not HTTPS."""
    pass

class InvalidPortError(TransportSecurityError):
    """Raised when target port is not in approved ports."""
    pass

class UserInfoDisallowedError(TransportSecurityError):
    """Raised when URL contains embedded userinfo (username/password)."""
    pass

class RedirectLimitExceeded(TransportSecurityError):
    """Raised when redirect loop or depth exceeds limit."""
    pass


def is_ip_disallowed(
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address,
    allow_loopback_for_testing: bool = False
) -> Tuple[bool, str]:
    """
    Evaluates whether an IP address is disallowed.
    Inspects loopback, private, link-local, CGNAT (100.64.0.0/10), cloud metadata, and IPv4-mapped IPv6.
    """
    # 1. Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1 or ::ffff:100.64.1.1)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        ip_obj = ip_obj.ipv4_mapped

    # 2. Check Cloud Metadata IP explicitly
    if str(ip_obj) == "169.254.169.254":
        return True, f"Cloud metadata service IP blocked: {ip_obj}"

    # 3. Check CGNAT range (100.64.0.0/10)
    if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in CGNAT_NETWORK:
        return True, f"Carrier-Grade NAT (CGNAT) address blocked: {ip_obj}"

    # 4. Standard disallowed checks
    if ip_obj.is_loopback:
        if not allow_loopback_for_testing:
            return True, f"Loopback address blocked: {ip_obj}"
        return False, ""
    if ip_obj.is_private and not allow_loopback_for_testing:
        return True, f"Private network address blocked: {ip_obj}"
    if ip_obj.is_link_local:
        return True, f"Link-local address blocked: {ip_obj}"
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
    approved_ports: Optional[Set[int]] = None,
    allow_loopback_for_testing: bool = False,
    dns_resolver=socket.getaddrinfo
) -> Tuple[str, int, List[str]]:
    """
    Validates URL scheme, port, userinfo, registry whitelisting, and IP safety.
    Returns (hostname, port, validated_ip_list).
    """
    parsed = urllib.parse.urlparse(url)

    # 1. Scheme check
    if parsed.scheme.lower() != "https":
        raise InsecureProtocolError(f"Insecure scheme '{parsed.scheme}'. Only 'https' is permitted.")

    # 2. UserInfo check
    if parsed.username or parsed.password or "@" in (parsed.netloc.split(":")[0] if parsed.netloc else ""):
        raise UserInfoDisallowedError("Userinfo (username/password) in URL is strictly prohibited.")

    hostname = parsed.hostname
    if not hostname:
        raise UnauthorizedTargetError("Missing hostname in target URL.")

    # 3. Port check
    port = parsed.port or 443
    valid_ports = approved_ports or DEFAULT_APPROVED_PORTS
    if port not in valid_ports:
        raise InvalidPortError(f"Port {port} is not in approved ports: {valid_ports}")

    # 4. Supplier Registry Check
    allowed_hosts = SUPPLIER_REGISTRY_WHITELIST.get(supplier_id)
    if not allowed_hosts or hostname.lower() not in allowed_hosts:
        raise UnauthorizedTargetError(
            f"Host '{hostname}' is not authorized for supplier '{supplier_id}'. Allowed: {allowed_hosts}"
        )

    # 5. IP Literal Hostname Check
    try:
        ip_literal = ipaddress.ip_address(hostname)
        disallowed, reason = is_ip_disallowed(ip_literal, allow_loopback_for_testing=allow_loopback_for_testing)
        if disallowed:
            raise SSRFBlockedError(reason)
        if not allow_loopback_for_testing:
            raise UnauthorizedTargetError("IP literals are not permitted as hostnames in supplier endpoints.")
        return hostname, port, [str(ip_literal)]
    except ValueError:
        pass  # Hostname is a valid domain name

    # 6. Resolve DNS and inspect ALL IP answers
    try:
        addr_info = dns_resolver(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise TransportSecurityError(f"DNS resolution failure for host '{hostname}': {e}")

    validated_ips: List[str] = []
    for entry in addr_info:
        sockaddr = entry[4]
        raw_ip = sockaddr[0]
        ip_obj = ipaddress.ip_address(raw_ip)

        disallowed, reason = is_ip_disallowed(ip_obj, allow_loopback_for_testing=allow_loopback_for_testing)
        if disallowed:
            raise SSRFBlockedError(f"Host '{hostname}' resolved to disallowed IP {raw_ip}: {reason}")

        if raw_ip not in validated_ips:
            validated_ips.append(raw_ip)

    if not validated_ips:
        raise TransportSecurityError(f"No valid IP addresses resolved for host '{hostname}'.")

    return hostname, port, validated_ips


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """
    Subclass of HTTPSConnection that establishes TCP connection directly to a pre-validated IP
    while preserving original hostname for TLS SNI, certificate validation, and HTTP Host header.
    Prevents DNS rebinding and secondary uncontrolled lookups.
    """

    def __init__(
        self,
        hostname: str,
        pinned_ip: str,
        port: int = 443,
        timeout: float = 10.0,
        ssl_context: Optional[ssl.SSLContext] = None
    ):
        super().__init__(
            host=pinned_ip,
            port=port,
            timeout=timeout,
            context=ssl_context or ssl.create_default_context()
        )
        self.target_hostname = hostname
        self.pinned_ip = pinned_ip

    def connect(self):
        """Creates TCP socket to pinned_ip, then wraps with TLS using target_hostname for SNI."""
        sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.target_hostname)


class PinnedHTTPTransport:
    """High-level transport client enforcing IP pinning, redirect policies, and credential security."""

    def __init__(
        self,
        supplier_id: str,
        approved_ports: Optional[Set[int]] = None,
        allow_loopback_for_testing: bool = False,
        ssl_context: Optional[ssl.SSLContext] = None,
        dns_resolver=socket.getaddrinfo
    ):
        self.supplier_id = supplier_id
        self.approved_ports = approved_ports or DEFAULT_APPROVED_PORTS
        self.allow_loopback_for_testing = allow_loopback_for_testing
        self.ssl_context = ssl_context
        self.dns_resolver = dns_resolver

    def send_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
        follow_redirects: bool = False,
        max_redirects: int = 3,
        timeout: float = 10.0
    ) -> Tuple[int, Dict[str, str], str]:
        """
        Sends HTTPS request to validated, pinned IP.
        Optionally follows redirects up to max_redirects while re-validating and stripping credentials across origins.
        """
        curr_url = url
        curr_headers = dict(headers or {})
        redirects_followed = 0

        while True:
            # 1. Validate target
            hostname, port, validated_ips = validate_target_url(
                curr_url,
                self.supplier_id,
                approved_ports=self.approved_ports,
                allow_loopback_for_testing=self.allow_loopback_for_testing,
                dns_resolver=self.dns_resolver
            )

            # Pin the first validated IP
            pinned_ip = validated_ips[0]

            parsed = urllib.parse.urlparse(curr_url)
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"

            # Set Host header explicitly to original hostname
            req_headers = dict(curr_headers)
            req_headers["Host"] = f"{hostname}:{port}" if port != 443 else hostname

            # 2. Connect to pinned IP
            conn = PinnedHTTPSConnection(
                hostname=hostname,
                pinned_ip=pinned_ip,
                port=port,
                timeout=timeout,
                ssl_context=self.ssl_context
            )

            try:
                conn.request(method, path, body=body, headers=req_headers)
                resp = conn.getresponse()
                status = resp.status
                resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                resp_body = resp.read().decode('utf-8')
            finally:
                conn.close()

            # 3. Handle Redirects
            if status in (301, 302, 303, 307, 308):
                if not follow_redirects:
                    return status, resp_headers, resp_body

                redirects_followed += 1
                if redirects_followed > max_redirects:
                    raise RedirectLimitExceeded(f"Redirect limit of {max_redirects} exceeded")

                location = resp_headers.get("location")
                if not location:
                    return status, resp_headers, resp_body

                # Resolve relative redirect
                next_url = urllib.parse.urljoin(curr_url, location)

                # Origin comparison: (scheme, hostname.lower(), port)
                orig_parsed = urllib.parse.urlparse(curr_url)
                next_parsed = urllib.parse.urlparse(next_url)

                orig_origin = (orig_parsed.scheme.lower(), orig_parsed.hostname.lower(), orig_parsed.port or 443)
                next_origin = (next_parsed.scheme.lower(), next_parsed.hostname.lower() if next_parsed.hostname else "", next_parsed.port or 443)

                # If origin changes, strip sensitive credentials
                if orig_origin != next_origin:
                    sensitive = {"authorization", "x-api-key", "cookie", "proxy-authorization"}
                    for s in sensitive:
                        curr_headers.pop(s, None)
                        for k in list(curr_headers.keys()):
                            if k.lower() == s:
                                del curr_headers[k]

                curr_url = next_url
                # For 303, change method to GET
                if status == 303 and method != "GET":
                    method = "GET"
                    body = None
                continue

            return status, resp_headers, resp_body
