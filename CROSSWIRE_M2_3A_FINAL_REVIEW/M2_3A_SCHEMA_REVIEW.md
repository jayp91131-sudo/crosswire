# CROSSWIRE 2.3A — SCHEMA ARCHITECTURE & INVARIANT REVIEW
## Technical Review of Tables, Composite Foreign Keys, Indexes, Triggers, and Invariant Mappings

**Database Engine:** SQLite 3.53.1  
**Total Tables:** 21  
**Total Indexes:** 37  
**Total Triggers:** 12  
**Referential Status:** 100% Validated (0 Foreign Key Violations)  

---

## 1. Table Architecture by Domain

### Domain 1: Preserved Milestone 2.2 Baseline (7 Tables)
1. `agencies`: Agency registry, API credentials, creation timestamps.
2. `booking_records`: Canonical parent booking entity (`booking_id`, `agency_id`, `status`, `total_amount_minor_units`, `currency`).
3. `wallet_holds`: Parent pre-authorization financial hold on agency credit line.
4. `payment_intents`: Durable customer card/wallet authorization intent.
5. `booking_operation_claims`: Disaggregated worker idempotency and lifecycle claims.
6. `voucher_records`: Immutable voucher entitlement issued upon confirmation.
7. `ledger_events`: Append-only cryptographically signed financial event stream.

### Domain 2: Provider Accounts & Scoped Authorizations (4 Tables)
8. `supplier_provider_accounts`: Direct provider accounts (`supplier_id`, `provider_account_id`, `is_global`, `is_active`).
9. `agency_provider_account_authorizations`: Agency-scoped supplier credentials (`agency_id`, `supplier_id`, `provider_account_id`, `is_enabled`).
10. `payment_provider_accounts`: Payment gateway merchant accounts (`payment_method`, `provider_account_id`, `is_global`, `is_active`).
11. `agency_payment_account_authorizations`: Agency-scoped payment credentials (`agency_id`, `payment_method`, `provider_account_id`, `is_enabled`).

### Domain 3: Multi-Child Booking Intent & Outbox (4 Tables)
12. `parent_stay_coverage_specs`: Continuous date/room coverage requirements for parent booking.
13. `child_supplier_intents`: Disaggregated supplier segment intents (`child_intent_id`, `parent_booking_id`, `agency_id`, `supplier_id`, `provider_account_id`, `status`).
14. `child_segment_specs`: Room type, occupancy, check-in, check-out specifications per child intent.
15. `child_operation_outbox`: Durable outbox queue (`operation_id`, `logical_operation_id`, `attempt_id`, `operation_type`, `supplier_capability`, `status`, `leased_by`, `lease_expires_at`, `fencing_token`, `supplier_idempotency_key`, `payload_hash`).

### Domain 4: Two-Stage Observation & Provenance (3 Tables)
16. `child_intent_pricing_snapshots`: Exactly one authoritative pricing allocation per child (`parent_booking_id`, `child_intent_id`, `amount_minor_units`).
17. `supplier_reservation_observations`: Append-only raw observations from workers, webhooks, or manual reconciliation (`observation_id`, `lifecycle_outcome`, `supplier_reservation_id`, `reconciliation_provenance`).
18. `supplier_observation_validations`: Immutable validation audit logs (`validation_id`, `observation_id`, `validation_status` IN ('VALIDATED', 'REJECTED', 'INCONCLUSIVE')).

### Domain 5: Authoritative Resolution & Payment Outbox (3 Tables)
19. `child_authoritative_resolution_history`: Append-only history of validated resolutions (`resolution_history_id`, `validation_status = 'VALIDATED'`).
20. `child_current_authoritative_resolution`: Current authoritative pointer per child intent (`child_intent_id` PRIMARY KEY, `active_resolution_history_id`, `fencing_token`).
21. `parent_payment_outbox`: Durable gateway payment outbox (`payment_operation_id`, `parent_booking_id`, `agency_id`, `payment_intent_id`, `gateway_idempotency_key`).

---

## 2. Composite Foreign Keys & Tenant Isolation

To strictly enforce multi-tenant isolation and prevent cross-agency or cross-booking data mixing, composite foreign keys link child and parent entities:

```
[agencies] (agency_id)
    ▲
    │ (agency_id)
[booking_records] (booking_id, agency_id)
    ▲
    │ (parent_booking_id, agency_id)
[child_supplier_intents] (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
    ▲
    │ (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
[child_operation_outbox]
    ▲
    │ (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
[supplier_reservation_observations]
    ▲
    │ (validation_id, observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, validation_status)
[supplier_observation_validations]
    ▲
    │ (resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, observation_id, lifecycle_status, supplier_reservation_id)
[child_authoritative_resolution_history]
    ▲
    │ (active_resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id, active_observation_id, lifecycle_status, supplier_reservation_id)
[child_current_authoritative_resolution]
```

Any attempt to associate a child intent, outbox item, observation, or resolution with a mismatched agency or parent booking is rejected by SQLite's foreign-key engine (`FOREIGN KEY constraint failed`).

---

## 3. Database Triggers (12 Total)

### Immutability Protection Triggers (6)
1. `trg_prevent_update_observations`: Aborts `UPDATE` on `supplier_reservation_observations`.
2. `trg_prevent_delete_observations`: Aborts `DELETE` on `supplier_reservation_observations` (also blocks `INSERT OR REPLACE`).
3. `trg_prevent_update_validations`: Aborts `UPDATE` on `supplier_observation_validations`.
4. `trg_prevent_delete_validations`: Aborts `DELETE` on `supplier_observation_validations`.
5. `trg_prevent_update_res_history`: Aborts `UPDATE` on `child_authoritative_resolution_history`.
6. `trg_prevent_delete_res_history`: Aborts `DELETE` on `child_authoritative_resolution_history`.

### Account Authorization Enforcement Triggers (5)
7. `trg_check_supplier_account_authorization`: `BEFORE INSERT ON child_supplier_intents` — verifies active global or enabled agency grant.
8. `trg_check_outbox_dispatch_authorization`: `BEFORE INSERT ON child_operation_outbox` — verifies active authorization at queue insertion.
9. `trg_check_outbox_status_dispatch_authorization`: `BEFORE UPDATE OF status ON child_operation_outbox WHEN NEW.status = 'IN_PROGRESS'` — verifies authorization has not been revoked before worker dispatch.
10. `trg_check_payment_account_authorization`: `BEFORE INSERT ON parent_payment_outbox` — verifies active payment gateway grant.
11. `trg_check_payment_outbox_status_dispatch_authorization`: `BEFORE UPDATE OF status ON parent_payment_outbox WHEN NEW.status = 'IN_PROGRESS'` — verifies payment authorization has not been revoked before dispatch.

### Concurrency & Fencing Trigger (1)
12. `trg_prevent_stale_fencing_token_resolution`: `BEFORE UPDATE OF fencing_token ON child_current_authoritative_resolution WHEN NEW.fencing_token < OLD.fencing_token` — blocks regressions from late-arriving workers.

---

## 4. Invariant Mapping Crosswalk

| Canonical Invariant | Description | Database Enforcement Mechanism | Test Evidence |
| :--- | :--- | :--- | :--- |
| **INV-1.2** | Tenant & Parent Referential Isolation | Composite Foreign Keys across (parent, child, agency) | `test_01`, `test_27` |
| **INV-1.6** | Provider Account Authorization | Dynamic evaluation triggers on INSERT & UPDATE | `test_02`, `test_03`, `test_04`, `test_05`, `test_30`, `test_31`, `test_32`, `test_34` |
| **INV-2.1** | Append-Only Raw Observations | Unconditional BEFORE UPDATE & DELETE triggers | `test_06`, `test_07`, `test_08`, `test_22` |
| **INV-2.2** | Immutable Validation Audit | Unconditional BEFORE UPDATE & DELETE triggers | `test_23`, `test_24` |
| **INV-2.3** | Two-Stage Authoritative Resolution | Filtered composite FK requiring `validation_status = 'VALIDATED'` | `test_09`, `test_10`, `test_11` |
| **INV-2.4** | Resolution Pointer Referential Integrity | 9-column composite FK linking pointer to history | `test_16`, `test_25`, `test_26` |
| **INV-2.5** | Exactly One Active Reservation | Partial unique index on `(supplier_id, provider_account_id, supplier_reservation_id)` | `test_21` |
| **INV-3.1** | Capability-Aware Idempotency Keys | Table CHECK constraint on `supplier_idempotency_key` | `test_28`, `test_29` |
| **INV-3.2** | Operation & Attempt Lineage | Composite FK linking attempt, logical key, and op type | `test_12`, `test_13` |
| **INV-3.3** | Worker Fencing & Monotonicity | `fencing_token` incrementing assertions & trigger | `test_17`, `test_18`, `test_33` |
| **INV-4.1** | 1:1 Child Pricing Allocation | `UNIQUE(parent_booking_id, child_intent_id)` | `test_19`, `test_20` |
| **INV-5.1** | Preserved Signed-Ledger Stream | M2.2 `ledger_events` table preserved intact | M2.2 baseline tests |
| **INV-5.7** | Double-Entry Ledger Decomposition | Deferred pending explicit product signoff | Verified deferred |

---

## 5. Known Limitations
1. Single-hotel, single-property, single-currency scope only (M2.3 requirement).
2. Outbox background workers and HTTP polling loops are not yet part of M2.3A (deferred to M2.3B).
3. SQLite in-memory constraints require strict application of `PRAGMA foreign_keys = ON;` on every database connection, managed through `db_connection.py`.
