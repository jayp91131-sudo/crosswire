# CROSSWIRE M2.3B — SOURCE CHANGE INVENTORY

## 1. Executive Summary

Milestone 2.3B implements the multi-supplier transaction execution outbox engine for Crosswire. All implementation files are strictly isolated within the new `m2_3b/` package tree and root review artifacts.

- **M2.2 Baseline Modification Status:** 0 modifications across all 64 files in `engine/`, `tests/`, `scenarios/`, and `target_platform/`.
- **M2.3B New Source Code Files:** 11 Python implementation modules, 1 SQL schema definition.
- **M2.3B Test Suite:** 7 test modules comprising 45 unit, concurrency, and integration tests.
- **Supporting Deliverable Reports:** 4 markdown reports, 1 test execution log, 1 requirements manifest, 1 review README.
- **Test Results Summary:**
  - M2.3B Test Suite: 45 passed, 0 failed, 0 skipped (100% pass rate).
  - M2.2 Baseline Test Suite: 196 passed, 0 failed, 0 skipped (100% pass rate).
  - Total Verified Tests: 241 passed, 0 failed.

---

## 2. Component Directory Structure

```
m2_3b/
├── __init__.py
├── coordinator/
│   ├── __init__.py
│   └── parent_saga_coordinator.py        # Parent transaction coordinator & saga compensator
├── evidence/
│   ├── __init__.py
│   ├── coverage_oracle.py                # Single-hotel, single-currency coverage validator
│   ├── observation_validator.py          # Two-tier syntactic & semantic evidence validator
│   ├── observation_writer.py             # Append-only observation storage & database triggers
│   └── resolution_manager.py             # Monotonic authoritative resolution pointer manager
├── outbox/
│   ├── __init__.py
│   ├── dispatcher.py                     # Outbox dispatch engine with exponential backoff
│   └── outbox_manager.py                 # Outbox lease acquisition, heartbeat, & recovery
├── schema/
│   ├── __init__.py
│   ├── db_connection.py                  # Foreign-key enforced SQLite connection factory
│   └── schema_m2_3a.sql                  # M2.3A relational schema with immutability triggers
├── suppliers/
│   ├── __init__.py
│   ├── base_adapter.py                   # Supplier adapter interface & capability models
│   └── synthetic_supplier.py             # Three capability-aware synthetic supplier adapters
├── tests/
│   ├── __init__.py
│   ├── test_coverage_oracle.py           # 7 coverage & occupancy invariant tests
│   ├── test_crash_recovery_concurrency.py# 10 worker crash, lease race, & recovery tests
│   ├── test_helpers.py                   # In-memory test harness & schema initialization
│   ├── test_outbox_engine.py             # 7 atomic leasing, fencing, & auth gate tests
│   ├── test_parent_coordinator.py        # 5 saga lifecycle, compensation, & hold tests
│   ├── test_pinned_transport.py          # 10 DNS pinning, SSRF, & timeout tests
│   └── test_supplier_capabilities.py     # 6 adapter capability & retry policy tests
└── transport/
    ├── __init__.py
    └── pinned_transport.py               # SSRF-safe HTTP client with socket-level DNS pinning
```

---

## 3. Detailed Source File Inventory & Cryptographic Hashes

### 3.1 M2.3B Core Implementation Modules

| Relative Path | Lines | Bytes | SHA-256 Checksum | Component Role |
| :--- | :---: | :---: | :--- | :--- |
| `m2_3b/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Package root initialization |
| `m2_3b/coordinator/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Coordinator package initialization |
| `m2_3b/coordinator/parent_saga_coordinator.py` | 251 | 11,722 | `f9e09bc0fbddb1203aef6f54ba69372f1c727734ce1891af9c10f42e36254539` | Parent saga state machine, child status aggregation, compensation dispatch, financial hold management |
| `m2_3b/evidence/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Evidence package initialization |
| `m2_3b/evidence/coverage_oracle.py` | 227 | 9,437 | `f4e041375e013d0ec554b9b5e4fa554b1cd9adee75f9099d37a1b988236b945e` | Temporal continuity checker, gap/overlap detector, per-room guest capacity auditor |
| `m2_3b/evidence/observation_validator.py` | 117 | 4,937 | `860ae6a15ebd73b79b32e20fd770f39d113fce3b0b62355642387e16b58b3f62` | Syntactic schema and semantic validation of supplier observations against child intent |
| `m2_3b/evidence/observation_writer.py` | 133 | 6,004 | `ab1b8f0299551238ade2f6fe58424db60dacfde7c9a90fe7a525cc641603f8ce` | Database writer for immutable observation records and validation outcome auditing |
| `m2_3b/evidence/resolution_manager.py` | 157 | 6,505 | `e0bd2b246bb3f2fec73134330ecdae9830d8b5068a59e2e9ca850bb581485074` | Append-only resolution history recorder with monotonic versioning and child status pointer |
| `m2_3b/outbox/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Outbox package initialization |
| `m2_3b/outbox/dispatcher.py` | 224 | 9,567 | `518a5d3276b285b588bb604a4d4aa0147dec1f6532aa8f25cf1552435fe7a605` | Outbox dispatch loop, adapter invocation, retry backoff calculation, reconciliation escalation |
| `m2_3b/outbox/outbox_manager.py` | 302 | 12,330 | `8f9f78e63b0effd3853aad174b93b4988c45013e83569ce7c352b9c2cd241770` | Outbox operation enrollment, atomic leasing, heartbeat extension, fencing token generation |
| `m2_3b/schema/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Schema package initialization |
| `m2_3b/schema/db_connection.py` | 47 | 1,531 | `e582cc13c66ae589b24ba5708a32a11f67dcedd4cec0b6c248c93d653bfa7976` | SQLite database connection factory configuring WAL mode, synchronous=NORMAL, foreign keys=ON |
| `m2_3b/schema/schema_m2_3a.sql` | 671 | 30,389 | `9479d230f0110598f35e25db6c4224b860a86c14429753b2fdfee16cd5b6ee8d` | Complete M2.3A relational schema DDL with strict check constraints and append-only triggers |
| `m2_3b/suppliers/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Suppliers package initialization |
| `m2_3b/suppliers/base_adapter.py` | 65 | 2,083 | `0e3dcb4ead85a4e02a367f565ddc06f483d5c118119a3b53b9b48e1131b41e99` | Abstract supplier adapter base class, adapter interface contract, supplier capabilities data classes |
| `m2_3b/suppliers/synthetic_supplier.py` | 219 | 8,406 | `08228d3377d66daae13a29cb775f07cdb0845f47fc05dbac7bc49dc4a74feee5` | Synthetic supplier implementations: synchronous direct, asynchronous polling, webhook/manual |
| `m2_3b/transport/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Transport package initialization |
| `m2_3b/transport/pinned_transport.py` | 316 | 11,961 | `1d55e5c9be29ea4b36afee7327aaf7247a2b6d2df7a5854e6992644413df5f71` | SSRF-safe custom HTTP transport with DNS resolution pinning and private network isolation |

### 3.2 M2.3B Test Suite Modules

| Relative Path | Lines | Bytes | SHA-256 Checksum | Test Focus |
| :--- | :---: | :---: | :--- | :--- |
| `m2_3b/tests/__init__.py` | 0 | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Test package initialization |
| `m2_3b/tests/test_coverage_oracle.py` | 266 | 14,754 | `44255aa0d4fef30a9e920aa4782831df948a7e3330a1f34807e1ebfc749652bc` | Date continuity, gap rejection, property matching, occupancy satisfaction, split stays |
| `m2_3b/tests/test_crash_recovery_concurrency.py` | 468 | 23,804 | `e5b180671bcb78169209b0ed5a1cfe708095da80c5c40b1a05b1fc6a3dc79129` | Worker crash recovery, lease race conditions, fencing token rejection, duplicate webhooks |
| `m2_3b/tests/test_helpers.py` | 140 | 6,598 | `260331f1e4bed37145e60a57b5038a48681a089a65daac7e07e1a638c58a2671` | Isolated SQLite fixture setup, agency/hotel/intent test seed data generation |
| `m2_3b/tests/test_outbox_engine.py` | 294 | 11,967 | `b03f802fa2444ccc9217ee43561ca0b32d86c202fa20857072dc10f26662e038` | Stable operation IDs, payload hashing, atomic leasing, peer recovery, account revocation |
| `m2_3b/tests/test_parent_coordinator.py` | 262 | 13,780 | `7c96884a7f9a440b9b9a9b176ddcd14bf7232af455b4412cf1482450af5e4995` | Parent saga coordination, compensation triggering, financial hold release, late confirmation |
| `m2_3b/tests/test_pinned_transport.py` | 282 | 10,735 | `8bc78c770a179be6ed0f5b103e71b9d8e34cb6ca55ddab38fe93228f9f0a8fe3` | DNS resolution pinning, RFC1918/link-local/multicast IP blocking, socket IP pinning |
| `m2_3b/tests/test_supplier_capabilities.py` | 264 | 11,593 | `8a1b5d28958b05b9caf794247b92054ce6073f040c4502f90193080c53998468` | Direct vs polling vs webhook adapters, idempotency key enforcement, polling termination |

### 3.3 Root Deliverables & Supporting Audit Documents

| File Name | Description |
| :--- | :--- |
| `M2_3B_OUTBOX_IMPLEMENTATION_REPORT.md` | Comprehensive architectural and implementation report for M2.3B |
| `M2_3B_FAILURE_INJECTION_MATRIX.md` | Complete failure injection catalog with adversarial recovery proofs |
| `M2_3B_NETWORK_TRANSPORT_SECURITY_REPORT.md` | Security assessment of SSRF defenses and DNS pinning |
| `M2_3B_TEST_RESULTS.txt` | Complete test execution console output for 45 M2.3B tests + 196 M2.2 tests |
| `SOURCE_CHANGE_INVENTORY.md` | This document; complete inventory and hash manifest |
| `GIT_STATUS_AND_BASELINE_DIFF_REPORT.md` | Verifiable audit report proving M2.2 frozen baseline integrity |
| `README.md` | Independent technical review guide with reproduction commands |
| `requirements.txt` | Verified Python runtime dependency manifest |
