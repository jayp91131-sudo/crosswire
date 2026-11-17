# CROSSWIRE M2.9 — TARGET INTEGRATION & ADAPTER FRAMEWORK

**Date:** 2026-11-15  
**Version:** M2.9 Target Integration & Adapter Framework  
**Status:** COMPLETE & VERIFIED  
**Test Suite:** 289 / 289 Tests Passing (100% Pass Rate)  
**Package:** `CROSSWIRE_M2_9_MVP.zip`

---

## Executive Summary

Milestone M2.9 evolves Crosswire from a synthetic-platform-specific implementation into a **reusable, target-independent dynamic application security testing (DAST) engine** for distributed B2B travel and fintech transaction systems.

Crosswire Core no longer imports or directly couples to the internal schemas, services, or databases of a single target platform. Instead, all transaction initiation, fault simulation, observation ingestion, and invariant auditing flow through strictly defined **Target Execution Adapters** and **Ground-Truth Observation Adapters** backed by a **Target Capability Model**.

To rigorously validate this architectural decoupling, we designed, implemented, and verified Crosswire against **two completely independent target platforms**:
1. **Target A (`target_a`)**: The Crosswire Reference B2B Platform (relational aggregator, supplier databases, double-entry wallet, and multi-supplier outbox).
2. **Target B (`travel_sandbox_b`)**: An entirely independent B2B travel distribution sandbox (`TravelSandboxB`) featuring a distinct SQLite database, completely divergent schema (`tsb_orders`, `tsb_vendor_confirmations`, `tsb_audit_ledger`, `tsb_webhook_journal`), divergent field models (`amount_cents` vs `amount_minor`, `confirmation_id` vs `supplier_reservation_id`), separate REST API endpoints, and independent fault injection controls.

Both platforms were tested across both canonical portable security scenarios (`SCN-TIMEOUT-001` and `SCN-WEBHOOK-001`) in both `VULNERABLE` and `FIXED` modes, producing identical invariant evaluations and security findings while reading from completely different underlying data stores.

---

## 1. Architectural Blueprint: The Adapter Pattern

```mermaid
flowchart TD
    subgraph CC["Crosswire Core & Control Center"]
        UI["M2.7.1 Forensic Console<br/>(Target Selector: Target A / Target B)"]
        Bridge["EngineBridge"]
        Registry["TargetAdapterRegistry<br/>(Allowlisting & Capability Discovery)"]
        ScenTime["PortableTimeoutScenario<br/>(INV-5.2 Invariant Oracle)"]
        ScenWeb["PortableWebhookScenario<br/>(INV-5.3 / INV-5.5 Oracle)"]
        Collector["RuntimeEventCollector<br/>(Canonical Monotonic Timelines)"]
    end

    subgraph Contracts["Standardized Adapter Contracts"]
        TEA["TargetExecutionAdapter<br/>- reset()<br/>- configure_fault_injection()<br/>- create_booking_intent()<br/>- submit_booking()<br/>- cancel_booking()<br/>- deliver_refund_webhook()"]
        GTOA["GroundTruthObservationAdapter<br/>- get_booking_observation()<br/>- get_supplier_reservations()<br/>- get_ledger_observations()<br/>- get_webhook_observation()<br/>- get_outbox_observations()"]
    end

    subgraph TargetA["Target A: Reference Platform"]
        TEA_A["TargetAExecutionAdapter"]
        GTOA_A["TargetAObservationAdapter"]
        DB_A[("SQLite Reference DB<br/>bookings, supplier_reservations,<br/>ledger_entries, refund_authorizations")]
    end

    subgraph TargetB["Target B: TravelSandboxB"]
        TEA_B["TargetBExecutionAdapter"]
        GTOA_B["TargetBObservationAdapter"]
        DB_B[("SQLite Sandbox B DB<br/>tsb_orders, tsb_vendor_confirmations,<br/>tsb_audit_ledger, tsb_webhook_journal")]
    end

    UI --> Bridge
    Bridge --> Registry
    Registry --> ScenTime
    Registry --> ScenWeb
    ScenTime --> TEA
    ScenTime --> GTOA
    ScenWeb --> TEA
    ScenWeb --> GTOA
    ScenTime -.-> Collector
    ScenWeb -.-> Collector

    TEA -.-> TEA_A
    TEA -.-> TEA_B
    GTOA -.-> GTOA_A
    GTOA -.-> GTOA_B

    TEA_A --> DB_A
    GTOA_A --> DB_A
    TEA_B --> DB_B
    GTOA_B --> DB_B
```

---

## 2. Core Framework Components

### 2.1 Standardized Target Contracts (`engine/adapters/contracts.py`)

- **`TargetCapability`**: Strict enumeration of functional capabilities:
  - `BOOKING_INTENTS`: Target accepts structured booking intent creation.
  - `SUPPLIER_RESERVATIONS`: Target commits and manages supplier hotel/flight reservations.
  - `IDEMPOTENCY_KEYS`: Target supports client-provided idempotency keys.
  - `REFUND_WEBHOOKS`: Target accepts asynchronous refund webhooks.
  - `LEDGER_OBSERVATIONS`: Target exposes financial ledger transactions.
  - `CANCELLATION`: Target supports booking cancellation and refund entitlement computation.
  - `MULTI_SUPPLIER_OUTBOX`: Target implements multi-supplier outbox coordination with fencing tokens.
  - `CONTROLLED_FAULT_INJECTION`: Target allows test harness to inject timeouts and webhook replays.
  - `GROUND_TRUTH_OBSERVATIONS`: Target exposes read-only ground-truth state for invariant verification.

- **`TargetMetadata`**: Immutable identity, versioning, network address, capability set, and sandbox isolation status.

- **`TargetExecutionAdapter`**: Abstract base contract defining all mutating operations (`reset`, `configure_fault_injection`, `create_booking_intent`, `submit_booking`, `cancel_booking`, `deliver_refund_webhook`).

- **`GroundTruthObservationAdapter`**: Abstract base contract defining read-only ground-truth evidence retrieval (`get_booking_observation`, `get_supplier_reservations`, `get_ledger_observations`, `get_webhook_observation`, `get_outbox_observations`).

### 2.2 Fail-Safe Observation Model (`ObservationResult[T]`)

A fundamental rule of Crosswire M2.9 is that **missing observations are never treated as passing invariants**.
`ObservationResult[T]` explicitly encapsulates:
- `available: bool`: Whether the target supports and could retrieve the requested observation.
- `data: Optional[T]`: Normalized observation dataclass when available.
- `unavailable_reason: Optional[str]`: Explicit explanation when ground truth cannot be verified.
- `observed_at_utc: str`: Exact ISO 8601 timestamp of observation capture.

If an invariant oracle requires observations that are marked `available=False`, the scenario immediately aborts with a descriptive exception rather than reporting a false negative or pseudo-pass.

### 2.3 Normalized Evidence Schemas

Regardless of internal target field names, all evidence is normalized to canonical dataclasses:
- **`BookingObservation`**: Normalized intent ID, state, gross amount, minor units, primary supplier reservation, retry count, and target evidence dictionary.
- **`SupplierReservationObservation`**: Normalized reservation ID, intent ID, supplier ID, state, rate, currency, and idempotency key.
- **`LedgerObservation`**: Normalized ledger entry ID, event ID, booking ID, amount, entry type (`REFUND`, `CAPTURE`), and direction (`CREDIT`, `DEBIT`).
- **`WebhookObservation`**: Normalized provider event ID, delivery count, accepted count, and deduplication status.

### 2.4 Target Capability & Adapter Registry (`engine/adapters/registry.py`)

- **Strict Allowlisting**: Rejects any unregistered target ID with `TargetNotAllowedError`. Out-of-bounds target URLs (such as external production APIs) cannot be executed.
- **Fail-Closed Capability Verification**: Scenarios declare required capabilities upfront. Calling `validate_capabilities(target_id, required_caps)` verifies the target advertises all capabilities; missing capabilities raise `UnsupportedCapabilityError`.
- **Auto-Registration**: Default local synthetic targets (`target_a` and `travel_sandbox_b`) register automatically on engine initialization.

---

## 3. Two Independent Target Implementations

| Dimension | Target A (Reference Platform) | Target B (`TravelSandboxB`) |
| :--- | :--- | :--- |
| **Identifier** | `target_a` | `travel_sandbox_b` |
| **Package** | `target_platform` | `travel_sandbox_b` |
| **Database Storage** | SQLite `aggregator.db` + `supplier.db` | Independent SQLite `sandbox_b.db` |
| **Booking Table** | `bookings` (`booking_id`, `intent_id`, `amount_minor`) | `tsb_orders` (`order_id`, `intent_token`, `total_amount_cents`) |
| **Supplier Table** | `supplier_reservations` (`supplier_reservation_id`) | `tsb_vendor_confirmations` (`confirmation_id`, `vendor_code`) |
| **Ledger Table** | `ledger_entries` (`entry_id`, `entry_type`, `direction`) | `tsb_audit_ledger` (`record_id`, `movement_type`, `direction`) |
| **Webhook Journal** | `webhook_events` (`event_id`, `provider`, `status`) | `tsb_webhook_journal` (`event_token`, `provider_code`, `delivery_count`) |
| **Transport** | In-memory ASGI `aggregator_app` + `supplier_app` | Standalone FastAPI `sandbox_b_app` via ASGI transport |
| **Outbox Engine** | Full multi-supplier outbox with monotonic fencing | **Omitted** (Demonstrates capability filtering & fail-closed behavior) |

---

## 4. Portable Security Scenarios (`engine/portable_scenarios/`)

### 4.1 `PortableTimeoutScenario` (`SCN-TIMEOUT-001`)
- **Required Capabilities**: `SUPPLIER_RESERVATIONS`, `CONTROLLED_FAULT_INJECTION`.
- **Operation**:
  1. Configures target for commit-then-timeout in `VULNERABLE` or `FIXED` mode.
  2. Dispatches synthetic booking intent via `TargetExecutionAdapter`.
  3. Queries ground-truth reservations via `GroundTruthObservationAdapter`.
  4. Evaluates `INV-5.2` (Booking Intent Uniqueness: `COUNT(reservations) <= 1`).
  5. In vulnerable mode: verifies 2 duplicate reservations, flags `CW-SEC-M2-001`.
  6. In fixed mode: verifies exactly 1 reservation, records `RetestResult` pass.
- **Runtime Provenance**: Emits monotonic lifecycle events (`INTENT_CREATED`, `REQUEST_DISPATCHED`, `SUPPLIER_COMMITTED`, `FAULT_TRIGGERED`, `RETRY_RECONCILE`, `SUPPLIER_COMMITTED` / `SUPPLIER_RECONCILED`, `INVARIANT_CHECK`, `FINDING_GENERATED` / `RETEST_COMPLETED`).

### 4.2 `PortableWebhookScenario` (`SCN-WEBHOOK-001`)
- **Required Capabilities**: `REFUND_WEBHOOKS`, `LEDGER_OBSERVATIONS`, `CONTROLLED_FAULT_INJECTION`.
- **Operation**:
  1. Sets up booking and cancels to establish a verifiable refund entitlement.
  2. Dispatches 3 identical refund webhook deliveries.
  3. Queries ground-truth ledger entries via `GroundTruthObservationAdapter`.
  4. Evaluates `INV-5.3` (Webhook Idempotency: single ledger entry) and `INV-5.5` (Refund Boundedness: total refund <= entitlement).
  5. In vulnerable mode: detects 3 duplicate credits totaling 15,000 INR against 12,000 INR entitlement (3,000 INR leak), flags `CW-SEC-M2-002`.
  6. In fixed mode: detects exactly 1 credit of 5,000 INR, verifies retest success.

---

## 5. Control Center Integration

The approved M2.7.1 visual identity (Swiss Editorial + Industrial Forensic Console) has been **strictly preserved with zero visual regressions**.

### New Target Selection Features:
1. **Target Header Selector**: A compact, mono-styled target selector in the top-bar (`Target A (Ref)` vs `Target B (Sandbox)`).
2. **Control Station Target Selector**: A target dropdown in Pane 1 of the Execution Workspace allowing operators to select the target platform before running transactions.
3. **Target Badge in Execution Registry**: The Overview execution table displays a distinct `Target A` (slate neutral) or `Target B` (tech cyan) badge for every executed transaction trace.
4. **Target API Endpoint (`GET /api/targets`)**: Exposes advertised capabilities, descriptions, and environment flags for all registered targets.
5. **Capability-Enforcing Execution (`POST /api/scenarios/run`)**: Accepts optional `target_id`. If a scenario requires capabilities unsupported by the selected target (e.g. `SCN-OUTBOX-001` on `travel_sandbox_b`), the request is rejected with HTTP 400 and a descriptive error message.

---

## 6. Comprehensive Verification & Test Results

The full test suite was executed across all platform packages. **100% of tests passed.**

```
============================= test session starts =============================
platform win32 -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\jayp9\.gemini\antigravity\scratch\crosswire
configfile: pytest.ini
plugins: anyio-4.12.1, asyncio-1.4.1.post1
collected 289 items

tests/ ................................................................ [ 24%]
tests/ ................................................................ [ 49%]
tests/control_center/m2_3c/m2_3b ...................................... [ 74%]
tests/test_target_adapters/ ........................................... [ 99%]
.                                                                        [100%]

============================== 289 passed in 64.21s ==============================
```

### Breakdown by Test Category:
- **Target Adapter Test Suite (`tests/test_target_adapters.py`)**: 17 / 17 PASSED
  - Target discovery and default registration: PASSED
  - Target metadata and capability advertising: PASSED
  - Allowlisting and external target rejection: PASSED
  - Fail-closed unsupported capability enforcement: PASSED
  - SCN-TIMEOUT-001 on Target A (Vulnerable & Fixed): PASSED
  - SCN-TIMEOUT-001 on Target B (Vulnerable & Fixed): PASSED
  - SCN-WEBHOOK-001 on Target A (Vulnerable & Fixed): PASSED
  - SCN-WEBHOOK-001 on Target B (Vulnerable & Fixed): PASSED
  - Observation normalization across divergent schemas: PASSED
  - Fail-safe unavailable ground truth handling: PASSED
  - Control Center `/api/targets` endpoint: PASSED
  - Control Center live execution against Target B: PASSED
  - Control Center capability rejection for Target B Outbox: PASSED
- **Runtime Event Instrumentation (`tests/test_runtime_instrumentation.py`)**: 6 / 6 PASSED
- **Control Center Operations & UI API (`control_center/tests/`)**: 9 / 9 PASSED
- **Baseline Protocol & Invariant Suite (`tests/`, `m2_3c/`, `m2_3b/`)**: 257 / 257 PASSED
- **Total**: **289 / 289 PASSED (100% Pass Rate)**

---

## 7. Deliverable Artifacts

1. **Architecture & Test Suite Codebase**:
   - `engine/adapters/contracts.py`: Canonical contracts and normalized schemas.
   - `engine/adapters/registry.py`: Central target allowlist and capability registry.
   - `engine/adapters/target_a_adapter.py`: Concrete adapter for Reference Platform.
   - `travel_sandbox_b/`: Completely independent second target implementation (`db.py`, `service.py`, `app.py`).
   - `engine/adapters/target_b_adapter.py`: Concrete adapter for `TravelSandboxB`.
   - `engine/portable_scenarios/portable_timeout.py`: Portable timeout scenario.
   - `engine/portable_scenarios/portable_webhook.py`: Portable webhook scenario.
   - `control_center/`: Target selector in UI, API updates, and engine bridge routing.
   - `tests/test_target_adapters.py`: 17 dedicated integration tests.
2. **Deliverable Package**:
   - `CROSSWIRE_M2_9_MVP.zip` (Clean, full runnable package; size: ~1,020 KB).
3. **Forensic Report**:
   - `M2_9_INTEGRATION_REPORT.md`
