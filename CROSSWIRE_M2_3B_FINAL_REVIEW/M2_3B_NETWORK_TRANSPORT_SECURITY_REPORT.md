# Crosswire 2.3B — Network Transport Security Report

**Document ID:** `M2_3B_NETWORK_TRANSPORT_SECURITY_REPORT.md`  
**Execution Timestamp:** 2026-11-14T12:37:52+05:30  
**Status:** FULLY IMPLEMENTED & FORMALLY VERIFIED (`10/10 PASSED`)  
**Target Module:** `m2_3b/transport/pinned_transport.py`  
**Test Suite:** `m2_3b/tests/test_pinned_transport.py`

---

## 1. Overview & Threat Model

In Milestone 2.3B, Crosswire dispatches real HTTP operations to heterogeneous supplier APIs across external networks. In enterprise distributed travel systems, outbound HTTP communication faces severe security threats:
1. **Server-Side Request Forgery (SSRF):** Malicious inputs or hijacked supplier endpoints redirecting requests to internal cloud metadata services, VPC peers, internal microservices, or loopback interfaces.
2. **DNS Rebinding & TOCTOU Race Attacks:** An attacker controlling DNS responds with a benign public IP on initial validation, but a fast subsequent DNS lookup inside the HTTP client resolves to an internal private IP (`127.0.0.1`, `169.254.169.254`).
3. **Carrier-Grade NAT (CGNAT) Exploitation:** Misconfigured RFC 6598 address spaces (`100.64.0.0/10`) routinely bypassed by naive `ip.is_private` checks, exposing internal routing nodes or cloud-provider transit gateways.
4. **Credential Leakage via Cross-Origin Redirects:** A compromised or spoofed supplier endpoint issuing a `302 Found` to an attacker-controlled origin, stealing `Authorization: Bearer` tokens and API keys.
5. **URL Userinfo Injection:** URLs formatted as `https://victim.com@attacker.com/` confusing naive parsers into communicating with unapproved destinations.

Crosswire 2.3B resolves these threats through a custom, socket-pinned transport layer: `PinnedHTTPTransport` and `PinnedHTTPSConnection`.

---

## 2. Architectural Defenses

### 2.1 RFC 6598 Carrier-Grade NAT (CGNAT) Blocking
Standard Python `ipaddress.IPv4Address.is_private` evaluates RFC 1918 networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), but treats RFC 6598 (`100.64.0.0/10`) as non-private.

Crosswire explicitly defines:
```python
CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in CGNAT_NETWORK:
    return True, f"Carrier-Grade NAT (CGNAT) address blocked: {ip_obj}"
```
Any address within `100.64.0.0` through `100.127.255.255` is immediately rejected.

### 2.2 IPv4-Mapped IPv6 Unwrapping
Attackers frequently bypass IPv4 filters by submitting IPv4-mapped IPv6 representations (e.g. `::ffff:127.0.0.1` or `::ffff:100.64.1.5`).
`is_ip_disallowed` unwraps mapped addresses before evaluation:
```python
if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
    ip_obj = ip_obj.ipv4_mapped
```
Both loopback and CGNAT addresses encapsulated in IPv6 are strictly detected and rejected.

### 2.3 Strict Target URL Validation
`validate_target_url` enforces:
1. **Protocol Check:** Only `https` is permitted. Plaintext `http://` raises `InsecureProtocolError`.
2. **Userinfo Prohibition:** URLs containing `@` in the authority or netloc (e.g. `https://admin:pass@api.expedia.com`) raise `UserInfoDisallowedError`.
3. **Approved Port Whitelist:** Default approved ports are strictly `{443}`. Ports like `8080`, `8443`, or `22` raise `InvalidPortError`.
4. **Supplier Whitelist Registry:** The target hostname must exist in `SUPPLIER_REGISTRY_WHITELIST` for the given supplier:
   - `SUP_EXPEDIA` -> `api.expedia.com`, `partner.api.expedia.com`
   - `SUP_HOTELBEDS` -> `api.hotelbeds.com`, `secure.hotelbeds.com`
   - `SUP_BOOKING` -> `distribution-xml.booking.com`, `supply-api.booking.com`
   - `PAY_STRIPE` -> `api.stripe.com`

### 2.4 Multi-Answer DNS Inspection (All-IP Validation)
DNS queries can return multiple A and AAAA records. If an attacker's DNS server returns:
```
Answer 1: 93.184.216.34 (Public IP)
Answer 2: 10.0.0.1 (Private Internal IP)
```
A standard HTTP client might attempt to connect to Answer 2 if Answer 1 fails.
`validate_target_url` inspects **every single IP address** returned by `socket.getaddrinfo`. If **any** returned IP is private, loopback, link-local, or CGNAT, the entire request is immediately aborted with `SSRFBlockedError`.

### 2.5 True Socket-Level IP Pinning
To defeat DNS rebinding TOCTOU vulnerabilities, `PinnedHTTPSConnection` bypasses standard hostname-based socket resolution:
```python
class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # 1. TCP socket connects directly to pre-validated IP
        sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        # 2. TLS handshake passes original hostname for SNI and certificate verification
        self.sock = self._context.wrap_socket(sock, server_hostname=self.target_hostname)
```
- **Connection Target:** `pinned_ip` (prevents secondary DNS lookup).
- **TLS SNI:** `target_hostname` (ensures certificate hostname matching and SNI routing succeed).
- **HTTP Host Header:** Explicitly set to `target_hostname` (prevents host header confusion).

### 2.6 Cross-Origin Redirect Credential Stripping
When following redirects (`301`, `302`, `303`, `307`, `308`):
1. The redirect target URL is recursively validated against the whitelisting and IP safety rules.
2. The origin tuple `(scheme, hostname.lower(), port)` is compared.
3. If the origin changes (e.g. from `https://api.expedia.com` to `https://partner.api.expedia.com`):
   - All sensitive headers (`Authorization`, `X-Api-Key`, `Cookie`, `Proxy-Authorization`) are stripped before issuing the redirected request.
4. Maximum redirect depth is capped at 3 hops; cycles trigger `RedirectLimitExceeded`.

---

## 3. Test Verification Summary

The network transport security implementation is verified by 10 automated tests in `m2_3b/tests/test_pinned_transport.py`:

| Test Name | Threat Tested | Verification Assertion | Status |
|---|---|---|---|
| `test_01_cgnat_rfc6598_ip_is_blocked` | CGNAT `100.64.0.0/10` probe | `SSRFBlockedError` raised on `100.64.1.5` | **PASSED** |
| `test_02_cloud_metadata_and_loopback_blocked` | Cloud metadata & loopback probe | `SSRFBlockedError` raised on `169.254.169.254` and `127.0.0.1` | **PASSED** |
| `test_03_ipv4_mapped_ipv6_blocked` | IPv6 mapped SSRF evasion | `SSRFBlockedError` raised on `::ffff:100.64.0.1` and `::ffff:127.0.0.1` | **PASSED** |
| `test_04_userinfo_in_url_rejected` | Userinfo credential injection | `UserInfoDisallowedError` raised on `user:pass@host` and `@host` | **PASSED** |
| `test_05_unapproved_port_rejected` | Non-standard port scan | `InvalidPortError` raised on port `8443` | **PASSED** |
| `test_06_insecure_http_rejected` | Cleartext HTTP transmission | `InsecureProtocolError` raised on `http://` | **PASSED** |
| `test_07_unauthorized_supplier_host_rejected` | Unregistered supplier hostname | `UnauthorizedTargetError` raised on unwhitelisted domain | **PASSED** |
| `test_08_dns_rebinding_mixed_answers_blocked` | Multi-answer DNS rebinding | `SSRFBlockedError` raised when answer includes `10.0.0.1` | **PASSED** |
| `test_09_redirect_cross_origin_credential_stripping` | Credential leakage on redirect | `Authorization` and `X-Api-Key` stripped on cross-origin redirect | **PASSED** |
| `test_10_real_socket_ip_pinning_over_local_tls` | Socket-level IP pinning & TLS SNI | Pinned socket connects directly to IP, completes TLS handshake | **PASSED** |

Execution time for the entire network security test suite: **0.81s**.
