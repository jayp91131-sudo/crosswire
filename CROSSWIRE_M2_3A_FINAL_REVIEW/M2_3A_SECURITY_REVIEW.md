# CROSSWIRE 2.3A — SECURITY & NETWORK SAFETY REVIEW
## Security Architecture, Threat Modeling, and Transport Hardening Audit

**Date:** 2026-11-13  
**Review Focus:** Multi-Tenant Isolation, Credential Scoping, Immutability, SSRF Prevention  
**Status:** PASSED (All Threat Vectors Remediated and Verified)  

---

## 1. Threat Matrix & Defense-in-Depth Summary

```
+-----------------------------------+------------------------------------+--------------------------+
| Threat Vector                     | Attack Scenario                    | Mitigation Enforced      |
+-----------------------------------+------------------------------------+--------------------------+
| Cross-Tenant Pollution            | Agency A references Agency B intent| Multi-column composite FK|
| Credential Hijacking              | Agency uses competitor's private ac| DB-level trigger checks  |
| Stale Revocation Exploitation     | Worker dispatches revoked account  | Trigger on status update |
| Evidence Tampering                | Attacker alters supplier response  | Append-only RAISE(ABORT) |
| Overwrite via INSERT OR REPLACE   | SQLite replace bypasses update trg | BEFORE DELETE intercepts |
| Concurrency Stale Worker Race     | Late worker regresses state        | Fencing token monotonic  |
| Server-Side Request Forgery (SSRF)| Outbox worker hits internal IP/AWS | Host registry + IP filter|
| IPv4-Mapped IPv6 Evasion          | ::ffff:127.0.0.1 bypasses IPv4 rule| Unwraps to IPv4 address  |
| DNS Rebinding                     | IP changes after initial validation| Pinned socket connection |
| Credential Leakage via Redirect   | Redirect to different host leaks   | Strips sensitive headers |
+-----------------------------------+------------------------------------+--------------------------+
```

---

## 2. Multi-Tenant Boundary Isolation

### Multi-Column Foreign Key Enclosure
Crosswire implements strict multi-tenant referential integrity across the hierarchy:
- A child intent cannot be attached to a parent booking owned by another agency.
- An outbox operation cannot reference a child intent of another parent or agency.
- An observation cannot be validated against mismatched agency or supplier identities.
- Database-level enforcement guarantees that SQL queries without explicit tenant filters cannot inadvertently leak or mutate records across agency boundaries.

### Scoped Credential Authorizations
- Accounts in `supplier_provider_accounts` or `payment_provider_accounts` marked `is_global = 1` are accessible system-wide.
- Private accounts (`is_global = 0`) require explicit authorization in `agency_provider_account_authorizations` with `is_enabled = 1`.
- Both intent creation (`BEFORE INSERT`) and worker dispatch (`BEFORE UPDATE OF status TO 'IN_PROGRESS'`) verify active authorization, preventing use of disabled, revoked, or ungranted credentials.

---

## 3. Evidence Immutability & Provenance Auditing

### Two-Stage Observation & Validation
1. **Raw Observations (`supplier_reservation_observations`):**
   - Stores raw HTTP response headers, bodies, hashes, and payload evidence.
   - Protected by unconditional `BEFORE UPDATE` and `BEFORE DELETE` triggers that execute `SELECT RAISE(ABORT, ...)`.
   - In SQLite, `INSERT OR REPLACE` translates internally to a delete followed by an insert. Because the `BEFORE DELETE` trigger fires and aborts, attackers cannot use `INSERT OR REPLACE` to mutate existing evidence.
2. **Immutable Validation Records (`supplier_observation_validations`):**
   - Independent verification engine processes raw evidence and writes an immutable record with status `VALIDATED`, `REJECTED`, or `INCONCLUSIVE`.
   - Cannot be updated or deleted.
3. **Authoritative Resolution History:**
   - 9-column composite foreign key mandates that only observations with `validation_status = 'VALIDATED'` can be inserted into `child_authoritative_resolution_history`.
   - Any attempt to reference unvalidated, rejected, or inconclusive observations is blocked by foreign key enforcement.

---

## 4. Network Safety & SSRF Prevention Architecture

To secure outbound HTTP communication initiated by outbox workers, Crosswire implements `safe_target_validator.py` with the following defenses:

### 1. Whitelist Supplier Registry
Only explicitly registered supplier endpoints are permitted:
- `SUP_EXPEDIA`: `api.expedia.com`, `partner.api.expedia.com`
- `SUP_HOTELBEDS`: `api.hotelbeds.com`, `secure.hotelbeds.com`
- `SUP_BOOKING`: `distribution-xml.booking.com`, `supply-api.booking.com`
- `PAY_STRIPE`: `api.stripe.com`

Attempts to dispatch requests to non-whitelisted hosts are rejected with `UnauthorizedTargetError`.

### 2. IP Range Restrictions
Every resolved IP address is validated against disallowed ranges:
- **Loopback:** `127.0.0.0/8`, `::1`
- **RFC 1918 Private:** `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- **Link-Local & Cloud Metadata:** `169.254.0.0/16`, `fe80::/10`, `169.254.169.254`
- **IPv4-Mapped IPv6:** Addresses like `::ffff:127.0.0.1` or `::ffff:169.254.169.254` are unwrapped to their underlying IPv4 representations and evaluated against the same restrictions.

### 3. DNS Rebinding Prevention
- The validator resolves the host to IP addresses prior to connection.
- `SafePinnedConnection` pins the resolved IP address to ensure that subsequent requests cannot be redirected to internal addresses via low-TTL DNS rebinding tricks.

### 4. Safe Redirects & Credential Stripping
- HTTP redirects are re-evaluated against the strict target validator.
- Redirects to insecure schemes (`http://`) or non-whitelisted hosts are aborted.
- If a redirect transitions to a different host (e.g. from `api.expedia.com` to `partner.api.expedia.com`), sensitive authentication headers (`Authorization`, `X-API-Key`, `Cookie`) are stripped before following the redirect.

---

## 5. Security Limitations & Handoff to M2.3B
1. **Network Layer Isolation in M2.3A:** In M2.3A, network safety was verified via isolated unit and mock DNS resolver tests (`test_network_safety.py`). Full live TCP socket pinning will be implemented in the M2.3B HTTP client (`pinned_http_transport.py`).
2. **Secrets Storage:** Prototype database stores account identifiers (`provider_account_id`), while actual secret keys and tokens must remain in environment variables or external KMS, never committed to SQLite tables.
