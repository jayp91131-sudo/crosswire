# Milestone 2.3 Architecture Proposal V3: Final Technical Addendum (Revision 3)
## Append-Only Evidence, Cancellation Capabilities, Provider-Account Integrity, Provenance, and Network Safety

**Document Reference:** `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_ADDENDUM_REVISION_3.md`  
**Author:** Principal Distributed Systems Architect, Travel Platform Transaction-Integrity Engineer, and Application Security Reviewer  
**Status:** Pre-Implementation Specification Gate (Revision 3)  
**Target Milestone:** Crosswire 2.3 (Multi-Supplier Routing & Split-Inventory Transaction Integrity)  
**Baseline Verification:** Milestone 2.2 Frozen Baseline (196 tests passing, 0 failed, 1 warning)

---

## 1. Concise List of Targeted Corrections

This document supersedes `MILESTONE_2_3_ARCHITECTURE_PROPOSAL_V3_FINAL_ADDENDUM_REVISION_2.md` and provides precise architectural corrections across seven targeted areas:

1. **Append-Only Supplier Observations:**
   - Eliminates the flawed `UNIQUE(child_intent_id)` constraint on observations, establishing an immutable append-only observation log.
   - Preserves duplicate, contradictory, stale, and late supplier responses.
   - Defines a deterministic resolution query to select the authoritative active observation.
   - Scopes reservation uniqueness to `(supplier_id, provider_account_id, supplier_reservation_id)` instead of child intent.
2. **Complete Cancellation Capability Matrix:**
   - Defines independent cancellation lifecycles for `IDEMPOTENT_WITH_LOOKUP`, `LOOKUP_ONLY`, and `NEITHER`.
   - Prohibits blind retries on cancellation timeouts.
   - Defines bounded lookup polling for `LOOKUP_ONLY` cancellations.
   - Enforces that a cancellation is **never** confirmed merely because an HTTP request was dispatched or timed out.
3. **Provider-Account Integrity & Payment Linkage:**
   - Adds `provider_account_id` to all relevant schema relationships (`child_supplier_intents`, `supplier_reservation_observations`, `child_operation_outbox`, and composite uniqueness constraints).
   - Scopes idempotency namespaces by `(supplier_id, provider_account_id)`.
   - Hardens `parent_payment_outbox` with explicit foreign-key linkage to `payment_intents` and provider/account scoping.
4. **Supplier Evidence Provenance:**
   - Formalizes authenticated supplier evidence beyond raw response hashes: authenticated transport identity, request correlation, provider-account scoping, response classification parsing, observation source provenance, and TLS session capture.
   - Requires the Coverage Oracle to consume validated, provenance-checked observations rather than trust raw child status labels.
5. **Hardened Wallet Finalization:**
   - Replaces all Python `assert` statements with explicit conditional checks and typed domain exceptions.
   - Enforces parent claim ownership, matching parent/claim fencing tokens, single active `HELD` hold, matching currency across all entities, and conditional SQL mutations with affected-row assertions (`rowcount == 1`).
   - Demonstrates idempotent handling of duplicate finalization calls.
6. **Network Safety and Dual-Stack IP Perimeter:**
   - Expands prohibited address policies to cover IPv4/IPv6 unspecified addresses (`0.0.0.0/8`, `::/128`), multicast (`224.0.0.0/4`, `ff00::/8`), broadcast (`255.255.255.255/32`), special-purpose/test ranges, and **IPv4-mapped IPv6 addresses** (`::ffff:0:0/96`).
   - Applies dual-stack validation to every resolved IP and every redirect hop.
   - Retains direct socket connection pinning to validated IPs with TLS SNI and Host header preservation.
7. **Gateway-Specific Payment Outcome Classification:**
   - Eliminates the assumption that HTTP 402 is universally authoritative.
   - Restricts transitions to `PAYMENT_FAILED` (and subsequent supplier leg compensation) to documented, definitive gateway decline codes.
   - Treats pending responses, timeouts, generic 4xx/5xx errors, and inconclusive lookups under strict ambiguity/reconciliation rules.

---

## 2. Corrected Schemas & Provider-Account Integrity

### 2.1 SQLite DDL with Provider-Account Scoping and Append-Only Evidence

```sql
-- Composite unique index on booking_records to support composite foreign keys
CREATE UNIQUE INDEX IF NOT EXISTS uidx_booking_records_id_agency 
ON booking_records (booking_id, agency_id);

-- 1. CHILD SUPPLIER INTENTS (Scoped by provider_account_id)
CREATE TABLE IF NOT EXISTS child_supplier_intents (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'DISPATCHED', 'CONFIRMED', 'DEFINITIVE_FAILURE', 
        'SUPPLIER_UNKNOWN', 'CANCELLATION_PENDING', 'CANCELLED', 'RECONCILIATION_REQUIRED'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_booking_id, agency_id) 
        REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT
);

-- Composite unique key to back downstream composite foreign keys
CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_intents_composite 
ON child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id);

-- 2. CHILD OPERATION OUTBOX (Authoritative Lease & Dispatch Engine)
CREATE TABLE IF NOT EXISTS child_operation_outbox (
    operation_id TEXT PRIMARY KEY,       -- Format: "{child_intent_id}:{operation_type}:{attempt}"
    logical_operation_id TEXT NOT NULL,  -- Stable Logical ID: "{child_intent_id}:{operation_type}"
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'CREATE_RESERVATION', 'CANCEL_RESERVATION', 'LOOKUP_RESERVATION', 'LOOKUP_CANCELLATION'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'EXHAUSTED', 'RECONCILIATION_REQUIRED'
    )),
    leased_by TEXT,
    lease_expires_at TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    supplier_idempotency_key TEXT NOT NULL,
    supplier_capability_snapshot TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    durable_outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_child_outbox_poll 
ON child_operation_outbox (status, lease_expires_at);

-- 3. APPEND-ONLY SUPPLIER RESERVATION OBSERVATIONS
-- NOTE: No UNIQUE constraint on child_intent_id! Multiple observations per leg are preserved.
CREATE TABLE IF NOT EXISTS supplier_reservation_observations (
    observation_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    supplier_reservation_id TEXT NOT NULL,
    confirmed_hotel_id TEXT NOT NULL,
    confirmed_check_in TEXT NOT NULL,
    confirmed_check_out TEXT NOT NULL,
    confirmed_room_type TEXT NOT NULL,
    confirmed_guest_capacity INTEGER NOT NULL CHECK (confirmed_guest_capacity > 0),
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    raw_response_hash TEXT NOT NULL,
    payload_snapshot TEXT NOT NULL,
    observation_source TEXT NOT NULL CHECK (observation_source IN (
        'WORKER_DISPATCH', 'LOOKUP_POLL', 'WEBHOOK_NOTIFICATION', 'MANUAL_RECONCILIATION'
    )),
    provenance_metadata_json TEXT NOT NULL,
    is_authoritative INTEGER NOT NULL DEFAULT 1 CHECK (is_authoritative IN (0, 1)),
    observed_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

-- Index to efficiently fetch observation history per child intent
CREATE INDEX IF NOT EXISTS idx_obs_child_history 
ON supplier_reservation_observations (child_intent_id, observed_at DESC);

-- Uniqueness scoped to the supplier domain and provider account
-- Guarantees a supplier cannot assign the same reservation reference to multiple bookings
CREATE UNIQUE INDEX IF NOT EXISTS uidx_obs_supplier_account_res 
ON supplier_reservation_observations (supplier_id, provider_account_id, supplier_reservation_id)
WHERE is_authoritative = 1;

-- 4. PARENT PAYMENT OUTBOX (Linked to payment_intents with provider/account scope)
CREATE TABLE IF NOT EXISTS parent_payment_outbox (
    payment_operation_id TEXT PRIMARY KEY,   -- Stable Logical ID: "{parent_booking_id}:CAPTURE_CARD"
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    payment_intent_id TEXT NOT NULL,
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN ('CAPTURE_CARD', 'LOOKUP_CARD_CAPTURE', 'REFUND_CARD')),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'PAYMENT_UNKNOWN', 'RECONCILIATION_REQUIRED'
    )),
    leased_by TEXT,
    lease_expires_at TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    gateway_idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    gateway_capture_reference TEXT,
    raw_response_hash TEXT,
    durable_outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_booking_id, agency_id) 
        REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT,
    FOREIGN KEY (payment_intent_id) 
        REFERENCES payment_intents (payment_intent_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_outbox_gateway_idemp 
ON parent_payment_outbox (payment_provider_id, provider_account_id, gateway_idempotency_key);

-- Uniqueness constraints to guarantee idempotent local finalization
CREATE UNIQUE INDEX IF NOT EXISTS uidx_ledger_booking_event 
ON ledger_events (booking_id, event_type);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_vouchers_booking 
ON voucher_records (booking_id);
```

---

## 3. Explicit State Transitions & Authoritative Observation Selection

### 3.1 Selection of Authoritative Supplier Observation
Because multiple observations may be recorded for a single child intent (e.g., dispatch observation, lookup polling observations, late webhook confirmations, or cancellation observations), the system determines the authoritative observation deterministically:

```sql
SELECT observation_id, supplier_reservation_id, confirmed_hotel_id, 
       confirmed_check_in, confirmed_check_out, confirmed_room_type, 
       confirmed_guest_capacity, raw_response_hash, payload_snapshot, 
       observation_source, provenance_metadata_json
FROM supplier_reservation_observations
WHERE child_intent_id = :child_intent_id
  AND is_authoritative = 1
ORDER BY observed_at DESC, fencing_token DESC
LIMIT 1;
```

#### Transition Invariants:
1. **Append-Only Immutability:** Historical observations are **never updated or deleted**. If new evidence arrives, a new row is appended with an incremented `fencing_token` and the current timestamp.
2. **Contradictory Observations:** If an observation arrives showing a reservation is active and a subsequent observation arrives showing it is cancelled, the observation log preserves both. The reduction query selects the latest verified observation.
3. **Stale Worker Defense:** A worker with an expired lease ($F_{\text{worker}} < F_{\text{active}}$) cannot advance `child_supplier_intents.status` or `booking_records.status` because all state mutations execute conditional SQL:
   ```sql
   UPDATE child_supplier_intents 
   SET status = 'CONFIRMED', updated_at = :now
   WHERE child_intent_id = :child_id 
     AND status = 'DISPATCHED';
   ```
   If the child was already compensated or cancelled by the coordinator, the conditional update affects 0 rows (`rowcount == 0`), neutralizing the stale worker write.

---

## 4. Failure and Recovery Behavior

### 4.1 Complete Cancellation Capability Matrix

Supplier cancellation operations must never assume success. The table below details the lifecycle of `CANCEL_RESERVATION` across the three capabilities:

| Dimension | `IDEMPOTENT_WITH_LOOKUP` | `LOOKUP_ONLY` | `NEITHER` |
| :--- | :--- | :--- | :--- |
| **First Dispatch** | Dispatches cancel request with stable cancellation key. | Dispatches cancel request. | Dispatches cancel request without idempotency guarantees. |
| **Stable Key Namespace** | `"cw-op-cancel:" + hash(supplier, account, child_id)` | `"cw-op-cancel:" + hash(supplier, account, child_id)` | None. |
| **Ambiguous Timeout / Socket Loss** | Eligible for bounded replay using identical cancellation key and payload. | **Do NOT replay cancel request.** Immediately enqueue `LOOKUP_CANCELLATION`. | **Zero retries. Zero lookups.** Immediately transition to `RECONCILIATION_REQUIRED`. |
| **Lookup Operation** | Query `/reservations/{id}` to verify cancellation status. | Query `/reservations/{id}` to verify cancellation status. | Not supported by supplier. |
| **Negative Lookup Behavior** | If lookup indicates reservation is still `ACTIVE`, retry cancel up to attempt cap (max 3). | If lookup indicates reservation is still `ACTIVE`, poll with backoff (e.g., 2s, 5s, 10s). | N/A |
| **Inconclusive Lookup** | If unresolved after attempt cap $\implies$ `RECONCILIATION_REQUIRED`. | If still `ACTIVE` or `404` after bounded polling $\implies$ `RECONCILIATION_REQUIRED`. | N/A |
| **Late Cancellation Confirmation** | Appended to observations log; child transitions from `RECONCILIATION_REQUIRED` to `CANCELLED`. | Appended to observations log; child transitions to `CANCELLED`. | Recorded via manual reconciliation tool; child marked `CANCELLED`. |
| **Financial Hold Treatment** | **Hold Retained** until cancellation is authoritatively confirmed or reconciled. | **Hold Retained** until cancellation is authoritatively confirmed or reconciled. | **Hold Retained** indefinitely until manual operational release. |

> [!CRITICAL]
> **Cancellation Invariant:** A cancellation must **never** be treated as confirmed merely because a cancel HTTP request was transmitted or timed out. If the cancellation status cannot be authoritatively verified, the leg must enter `RECONCILIATION_REQUIRED`, and the corresponding financial hold must remain locked to protect against supplier wholesale charges.

---

### 4.2 Gateway-Specific Payment Outcome Classification

HTTP status codes alone must not trigger destructive compensation actions. The engine enforces provider-specific response classifications:

```python
class PaymentOutcomeClassification(str, Enum):
    AUTHORITATIVE_SUCCESS = "AUTHORITATIVE_SUCCESS"
    AUTHORITATIVE_DECLINE = "AUTHORITATIVE_DECLINE"
    AMBIGUOUS_OUTCOME = "AMBIGUOUS_OUTCOME"

def classify_card_gateway_response(
    provider_id: str,
    status_code: int,
    response_body: dict
) -> PaymentOutcomeClassification:
    """
    Classifies payment gateway responses according to provider contract.
    Only explicit, documented decline codes authorize supplier cancellation.
    """
    # 1. Authoritative Success
    if status_code in (200, 201) and response_body.get("status") in ("succeeded", "captured"):
        return PaymentOutcomeClassification.AUTHORITATIVE_SUCCESS

    # 2. Authoritative Decline (Explicit Non-Retryable Rejection)
    # Generic HTTP 402 is NOT sufficient on its own without validating decline code
    decline_code = response_body.get("decline_code") or response_body.get("error_code")
    DEFINITIVE_DECLINE_CODES = {
        "card_declined", "expired_card", "incorrect_cvc", 
        "insufficient_funds", "account_closed", "stolen_card"
    }
    if status_code == 402 and decline_code in DEFINITIVE_DECLINE_CODES:
        return PaymentOutcomeClassification.AUTHORITATIVE_DECLINE

    # 3. Everything Else is AMBIGUOUS
    # Includes: timeouts (504), server crashes (500, 502), rate limits (429),
    # pending states ("requires_action", "processing"), and generic 400 Bad Request
    return PaymentOutcomeClassification.AMBIGUOUS_OUTCOME
```

---

### 4.3 Network Safety: Dual-Stack & Mapped Address Prohibitions

The target connection perimeter validates **every resolved IPv4 and IPv6 address** against all non-publicly routable and special-purpose IP ranges before socket creation.

#### Comprehensive Prohibited Address Ranges
- **IPv4 Unspecified & Current Network:** `0.0.0.0/8`
- **IPv4 Loopback:** `127.0.0.0/8`
- **IPv4 Private (RFC 1918):** `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- **IPv4 Link-Local / Cloud Metadata:** `169.254.0.0/16`
- **Carrier-Grade NAT (RFC 6598):** `100.64.0.0/10`
- **IETF Protocol Assignments & Benchmark:** `192.0.0.0/24`, `198.18.0.0/15`
- **Documentation Test Nets (TEST-NET-1, 2, 3):** `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`
- **IPv4 Multicast:** `224.0.0.0/4`
- **IPv4 Reserved / Future Use:** `240.0.0.0/4`
- **IPv4 Limited Broadcast:** `255.255.255.255/32`
- **IPv6 Unspecified:** `::/128`
- **IPv6 Loopback:** `::1/128`
- **IPv6 Unique Local (ULA):** `fc00::/7`
- **IPv6 Link-Local:** `fe80::/10`
- **IPv6 Multicast:** `ff00::/8`
- **IPv4-Mapped IPv6:** `::ffff:0:0/96` (The embedded IPv4 address is extracted and evaluated against all IPv4 prohibited rules above).

#### Direct Socket Connection Pinning
```python
def validate_and_pin_ip(hostname: str, port: int, is_testbed: bool) -> str:
    """
    Resolves DNS, verifies ALL returned IPv4 and IPv6 addresses, and returns
    a single pinned IP. Socket connects directly to this IP, eliminating rebinding.
    """
    addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    validated_ips = []

    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        ip_obj = ipaddress.ip_address(ip_str)

        # Unpack IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            ip_obj = ip_obj.ipv4_mapped

        if is_testbed:
            if not ip_obj.is_loopback:
                raise SecurityViolationError(f"Testbed target {ip_str} is not loopback")
        else:
            if is_prohibited_ip(ip_obj):
                raise SecurityViolationError(f"SSRF blocked: Host {hostname} resolved to prohibited IP {ip_str}")
        
        validated_ips.append(str(ip_obj))

    return validated_ips[0]
```

---

## 5. Supplier Evidence Provenance & Verification

A cryptographic hash of a raw HTTP response body proves only that the body has not mutated on disk; **it does not prove that the response originated from an authenticated supplier**.

### 5.1 Authenticated Supplier Evidence Components
To be accepted as authoritative evidence by the Coverage Oracle, an observation must satisfy seven provenance criteria:

1. **Transport Authenticity:** Outbound requests are dispatched over pinned TCP sockets using verified TLS certificate chains matching the registered supplier hostname.
2. **Account Scope Verification:** The observation records the active `provider_account_id` and matches the account configured for the child intent.
3. **Correlation Tracking:** The observation payload links `correlation_id`, `parent_booking_id`, and `child_intent_id`.
4. **Adapter Semantic Validation:** The supplier adapter parses and validates the response schema, verifying explicit confirmation codes (e.g. `status == 'CONFIRMED'`) and extracting structured reservation attributes.
5. **Cryptographic Payload Hash:** `raw_response_hash == sha256(payload_snapshot)`.
6. **Observation Source Tagging:** Explicitly records whether the observation was captured via `WORKER_DISPATCH`, `LOOKUP_POLL`, `WEBHOOK_NOTIFICATION`, or `MANUAL_RECONCILIATION`.
7. **Signature Verification (When Supported):** If the supplier provides cryptographic response signatures (e.g., HMAC-SHA256 headers), the signature is verified before the observation is recorded. If the supplier does not sign HTTP responses, provenance is established through TLS transport pinning and client-side audit metadata (`provenance_metadata_json`).

---

## 6. Hardened Local Wallet Finalization Pseudocode

The pseudocode below eliminates all Python `assert` statements, using explicit conditional checks, typed exceptions, affected-row assertions, and duplicate finalization handling:

```python
def finalize_wallet_booking_hardened(
    db: AggregatorDatabase,
    parent_booking_id: str,
    expected_fencing_token: int,
    worker_id: str
) -> FinalizeResult:
    """
    Executes atomic, strictly conditioned local wallet finalization.
    Guarantees no vouchers or ledger events are created if any precondition fails.
    """
    with db.begin_immediate() as conn:
        # 1. Verify Parent Record and Conditional State
        parent = conn.execute("""
            SELECT status, fencing_token, agency_id, total_amount, currency, payment_method, voucher_id
            FROM booking_records
            WHERE booking_id = ?
        """, (parent_booking_id,)).fetchone()
        
        if not parent:
            raise BookingNotFoundError(parent_booking_id)

        # Idempotent handling of duplicate finalization attempts
        if parent["status"] == "CONFIRMED":
            if not parent["voucher_id"]:
                raise InconsistentBookingStateError("Booking confirmed but missing voucher_id")
            return FinalizeResult(success=True, voucher_id=parent["voucher_id"], status="CONFIRMED")

        if parent["status"] != "IN_PROGRESS":
            raise InvalidBookingStateError(f"Parent {parent_booking_id} status is {parent['status']}")
        if parent["fencing_token"] != expected_fencing_token:
            raise StaleFencingTokenError(f"Stale fence: expected {expected_fencing_token}, got {parent['fencing_token']}")
        if parent["payment_method"] != "WALLET":
            raise PaymentMethodMismatchError(f"Payment method is {parent['payment_method']}, expected WALLET")

        # 2. Verify Parent Operation Claim Ownership
        claim = conn.execute("""
            SELECT claim_id, owner, fencing_token, status
            FROM booking_operation_claims
            WHERE booking_id = ? AND status = 'ACTIVE'
        """, (parent_booking_id,)).fetchone()
        
        if not claim:
            raise MissingActiveClaimError(f"No active claim found for {parent_booking_id}")
        if claim["owner"] != worker_id:
            raise StaleWorkerClaimError(f"Claim owned by {claim['owner']}, expected {worker_id}")
        if claim["fencing_token"] != expected_fencing_token:
            raise StaleWorkerClaimError(f"Claim fence {claim['fencing_token']} != expected {expected_fencing_token}")

        # 3. Verify Exactly One Active HELD Wallet Hold
        holds = conn.execute("""
            SELECT hold_id, amount, currency, status
            FROM wallet_holds
            WHERE booking_id = ? AND status = 'HELD'
        """, (parent_booking_id,)).fetchall()
        
        if len(holds) != 1:
            raise WalletHoldViolationError(f"Expected exactly 1 HELD hold for {parent_booking_id}, found {len(holds)}")
        hold = holds[0]
        
        # 4. Strict Currency and Minor-Unit Matching
        if hold["currency"] != parent["currency"]:
            raise CurrencyMismatchError(f"Hold currency {hold['currency']} != Booking currency {parent['currency']}")
        if hold["amount"] != parent["total_amount"]:
            raise FinancialDiscrepancyError(f"Hold amount {hold['amount']} != Booking total {parent['total_amount']}")

        # 5. Verify Coverage Spec and Authoritative Observations
        spec = conn.execute("SELECT * FROM parent_stay_coverage_specs WHERE parent_booking_id = ?", (parent_booking_id,)).fetchone()
        segments = conn.execute("SELECT * FROM child_segment_specs WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()
        
        # Retrieve Authoritative Observations
        authoritative_observations = {}
        for seg in segments:
            obs = conn.execute("""
                SELECT * FROM supplier_reservation_observations
                WHERE child_intent_id = ? AND is_authoritative = 1
                ORDER BY observed_at DESC, fencing_token DESC
                LIMIT 1
            """, (seg["child_intent_id"],)).fetchone()
            
            if not obs:
                raise MissingObservationError(f"Missing authoritative observation for child {seg['child_intent_id']}")
            authoritative_observations[seg["child_intent_id"]] = obs

        # Execute Coverage Oracle with validated evidence
        verify_slot_occupancy_and_coverage(spec, segments, authoritative_observations)

        # 6. Verify Child Pricing Allocations Reconcile Exactly
        pricing_rows = conn.execute("SELECT * FROM child_intent_pricing_snapshots WHERE parent_booking_id = ?", (parent_booking_id,)).fetchall()
        if len(pricing_rows) != spec["expected_children_count"]:
            raise PricingAllocationError(f"Expected {spec['expected_children_count']} pricing rows, got {len(pricing_rows)}")
        
        for p in pricing_rows:
            if p["currency"] != parent["currency"]:
                raise CurrencyMismatchError(f"Child pricing currency {p['currency']} != Parent {parent['currency']}")
                
        total_allocated = sum(p["agency_allocated_gross"] for p in pricing_rows)
        if total_allocated != parent["total_amount"]:
            raise FinancialReconciliationError(f"Allocated gross {total_allocated} != Parent gross {parent['total_amount']}")

        # 7. Atomic Conditional Mutations with Affected-Row Checks
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Update Wallet Hold: HELD -> CAPTURED
        res_hold = conn.execute("""
            UPDATE wallet_holds
            SET status = 'CAPTURED', updated_at = ?
            WHERE hold_id = ? AND status = 'HELD'
        """, (now_iso, hold["hold_id"]))
        if res_hold.rowcount != 1:
            raise ConcurrentMutationError("Failed to update wallet hold to CAPTURED")

        # Post Double-Entry Ledger Event (Enforced by UNIQUE(booking_id, event_type))
        conn.execute("""
            INSERT INTO ledger_events (event_id, agency_id, booking_id, event_type, amount, currency, created_at)
            VALUES (?, ?, ?, 'WALLET_CAPTURE', ?, ?, ?)
        """, (str(uuid.uuid4()), parent["agency_id"], parent_booking_id, -parent["total_amount"], parent["currency"], now_iso))

        # Issue Parent Voucher (Enforced by UNIQUE(booking_id))
        voucher_id = f"VOUCH-{uuid.uuid4().hex[:10].upper()}"
        conn.execute("""
            INSERT INTO voucher_records (voucher_id, booking_id, agency_id, hotel_id, check_in_date, check_out_date, status, issued_at)
            VALUES (?, ?, ?, ?, ?, ?, 'ISSUED', ?)
        """, (voucher_id, parent_booking_id, parent["agency_id"], spec["hotel_id"], spec["check_in_date"], spec["check_out_date"], now_iso))

        # Update Parent Booking Status conditionally on fencing token
        res_booking = conn.execute("""
            UPDATE booking_records
            SET status = 'CONFIRMED', voucher_id = ?, fencing_token = fencing_token + 1, updated_at = ?
            WHERE booking_id = ? AND status = 'IN_PROGRESS' AND fencing_token = ?
        """, (voucher_id, now_iso, parent_booking_id, expected_fencing_token))
        if res_booking.rowcount != 1:
            raise StaleFencingTokenError("Failed to advance parent booking status: fencing token mismatch")

        # Complete Operation Claim
        res_claim = conn.execute("""
            UPDATE booking_operation_claims
            SET status = 'COMPLETED', updated_at = ?
            WHERE booking_id = ? AND owner = ? AND fencing_token = ? AND status = 'ACTIVE'
        """, (now_iso, parent_booking_id, worker_id, expected_fencing_token))
        if res_claim.rowcount != 1:
            raise StaleWorkerClaimError("Failed to complete operation claim")

        return FinalizeResult(success=True, voucher_id=voucher_id, status="CONFIRMED")
```

---

## 7. Target Acceptance Test Matrix

> [!NOTE]
> All test counts below represent **implementation target criteria**, not claims of current passage.

| Category | Type | Target Test Case Description | Expected Assertion |
| :--- | :--- | :--- | :--- |
| **Append-Only Evidence** | **Positive** | Multiple observations appended for single child intent. | All observations preserved; selection query retrieves latest authoritative observation. |
| **Append-Only Evidence** | **Negative** | Duplicate reservation ID within same supplier and account. | Unique index `uidx_obs_supplier_account_res` raises `sqlite3.IntegrityError`. |
| **Cancellation Capabilities** | **Positive** | `IDEMPOTENT_WITH_LOOKUP` cancel retry on timeout. | Re-dispatch uses identical `cw-op-cancel:` key and payload; cancels reservation without error. |
| **Cancellation Capabilities** | **Negative** | `LOOKUP_ONLY` cancel experiences timeout. | Re-dispatch is suppressed; `LOOKUP_CANCELLATION` enqueued; negative lookup initiates bounded polling. |
| **Cancellation Capabilities** | **Negative** | `NEITHER` cancel experiences timeout. | Zero retries/lookups; leg immediately escalates to `RECONCILIATION_REQUIRED`; hold locked. |
| **Provider-Account Scope** | **Positive** | Two distinct provider accounts share same supplier ID. | Idempotency keys and reservation uniqueness are strictly partitioned by account. |
| **Provider-Account Scope** | **Negative** | Observation claims provider account not belonging to child intent. | Composite foreign key raises `sqlite3.IntegrityError`. |
| **Evidence Provenance** | **Positive** | Verified observation with matching TLS cert, account, and hash. | Oracle accepts observation as authoritative proof of fulfillment. |
| **Evidence Provenance** | **Negative** | Observation payload hash does not match `sha256(payload_snapshot)`. | Oracle rejects observation provenance; finalization aborts. |
| **Payment Classification** | **Positive** | Gateway returns HTTP 402 with `card_declined`. | Outbox marked `FAILED`; booking marked `PAYMENT_FAILED`; child legs compensated. |
| **Payment Classification** | **Negative** | Gateway returns HTTP 402 with generic/retryable error code. | Operation classified as `AMBIGUOUS_OUTCOME`; blind retries suppressed; enters `PAYMENT_UNKNOWN`. |
| **Dual-Stack Network Safety** | **Negative** | Target resolves to IPv4-mapped IPv6 address `::ffff:127.0.0.1`. | Extracted IPv4 loopback is detected and blocked with `SecurityViolationError`. |
| **Dual-Stack Network Safety** | **Negative** | Target resolves to IPv6 link-local `fe80::1`. | Prohibited IPv6 network check blocks connection. |
| **Dual-Stack Network Safety** | **Negative** | Target resolves to IPv4 broadcast `255.255.255.255`. | Prohibited broadcast check blocks connection. |
| **Hardened Wallet** | **Positive** | Normal wallet finalization. | Single `BEGIN IMMEDIATE` atomically captures hold, posts ledger, issues voucher. |
| **Hardened Wallet** | **Positive** | Duplicate finalization attempt on confirmed booking. | Idempotently returns existing voucher without re-posting ledger debits. |
| **Hardened Wallet** | **Negative** | Child pricing currency differs from parent booking currency. | `CurrencyMismatchError` raised; transaction rolled back; no voucher issued. |
| **M2.2 Frozen Baseline** | **Frozen** | All 35 legacy test suites. | 196 tests passing baseline preserved without modification. |

---

## 8. Remaining Assumptions, Risks, and External Contracts

1. **Gateway Query-by-Idempotency-Key Contract:** The two-phase card capture model assumes external card gateways support query-by-idempotency-key with read consistency within 15 seconds. If a gateway does not provide an authoritative lookup endpoint, capture timeouts must immediately escalate to manual operational reconciliation.
2. **Supplier Read Consistency:** For `LOOKUP_ONLY` suppliers, bounded polling assumes eventual consistency within 15 seconds. Suppliers with asynchronous or manual fulfillment workflows taking minutes or hours must be classified as `NEITHER` to prevent automated race conditions.
3. **OS Socket Factory Constraints:** Dual-stack IP connection pinning relies on Python standard library `socket.getaddrinfo` and `httpcore` socket creation. Environments with mandatory transparent corporate proxies must register proxy IP ranges in the allowlist.
4. **Scope Boundaries:** Hotel accommodations only; single property per logical booking; single currency (default USD); integer minor units (cents); zero FX float or conversion; no dynamic packaging.

---

## 9. Implementation Gate & Final Review Standard

### Component Readiness Evaluation

| Component | Technical Completeness | Remaining Blockers | Verdict |
| :--- | :--- | :--- | :--- |
| **1. Data Model & Append-Only Evidence** | Composite keys, provider account scoping, append-only observation log. | None. | **READY** |
| **2. Coverage & Occupancy Oracle** | Provenance verification, slot independence, $[in, out)$ continuity. | None. | **READY** |
| **3. Capability-Aware Outbox Engine** | Disaggregated leases, capability retry matrix, cancellation lifecycle. | None. | **READY** |
| **4. Hardened Wallet Finalization** | Conditional SQL, rowcount checks, single hold validation, currency matching. | None. | **READY** |
| **5. Card Payment Outbox** | Two-phase outbox, crash recovery matrix, decline code classification. | External gateway lookup SLA contract signoff. | **CONDITIONAL** |
| **6. Dual-Stack Pinned Transport** | Full IPv4/IPv6 prohibition, IPv4-mapped unpacking, SNI preservation. | None. | **READY** |

### **FINAL VERDICT: CONDITIONAL GO**

Implementation is authorized to proceed immediately for **Sub-Milestones 2.3A and 2.3B in local testbed environments**. 

Production deployment of the external card gateway integration is **conditionally gated** on formal verification of the payment gateway's query-by-idempotency-key SLA contract.

#### Authorized Implementation Sequence:
- **Phase 2.3A (Data Model & Schema Migration):** Apply SQLite additive DDL, append-only observations, provider-account scoping, and the provenance-aware coverage oracle. (Gate: 196 baseline tests remain 100% green).
- **Phase 2.3B (Outbox Engine & Connection Pinning):** Implement disaggregated operation leases, supplier capability adapters, dual-stack pinned IP HTTP transport, and hardened wallet finalization.
- **Phase 2.3C (Adversarial Test Suite):** Implement and execute the 60 target test cases covering capability modes, crash recovery, evidence validation, and SSRF defenses (Target Suite: 256 passing tests).

---
*End of Milestone 2.3 Architecture Proposal V3 Final Addendum (Revision 3).*
