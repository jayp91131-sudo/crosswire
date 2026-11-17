-- ============================================================================
-- CROSSWIRE 2.3A EXECUTABLE SCHEMA PROTOTYPE
-- Target: Disposable SQLite Database Schema for Multi-Supplier Split-Inventory
-- Preserves M2.2 signed-ledger baseline while establishing M2.3A relational integrity
-- ============================================================================

-- ============================================================================
-- 1. BASELINE M2.2 TABLES (Required for Foreign-Key Relationships)
-- ============================================================================
CREATE TABLE IF NOT EXISTS agencies (
    agency_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS booking_records (
    booking_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (agency_id) REFERENCES agencies (agency_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_booking_records_id_agency 
ON booking_records (booking_id, agency_id);

CREATE TABLE IF NOT EXISTS wallet_holds (
    hold_id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('HELD', 'CAPTURED', 'RELEASED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (booking_id, agency_id) REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS payment_intents (
    payment_intent_id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'AUTHORIZED', 'CAPTURED', 'FAILED', 'CANCELLED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (booking_id, agency_id) REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_intents_composite 
ON payment_intents (payment_intent_id, booking_id, agency_id, payment_provider_id, provider_account_id);

CREATE TABLE IF NOT EXISTS booking_operation_claims (
    claim_id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'COMPLETED', 'RELEASED', 'EXPIRED')),
    lease_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (booking_id) REFERENCES booking_records (booking_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS voucher_records (
    voucher_id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL UNIQUE,
    agency_id TEXT NOT NULL,
    hotel_id TEXT NOT NULL,
    check_in_date TEXT NOT NULL,
    check_out_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ISSUED',
    issued_at TEXT NOT NULL,
    FOREIGN KEY (booking_id, agency_id) REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS ledger_events (
    event_id TEXT PRIMARY KEY,
    transaction_reference TEXT UNIQUE NOT NULL,
    booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (booking_id, agency_id) REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_ledger_events_booking_type 
ON ledger_events (booking_id, event_type);

-- ============================================================================
-- 2. M2.3A PROVIDER ACCOUNT REGISTRIES & AUTHORIZATIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS supplier_provider_accounts (
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_global INTEGER NOT NULL DEFAULT 0 CHECK (is_global IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (supplier_id, provider_account_id)
);

CREATE TABLE IF NOT EXISTS agency_provider_account_authorizations (
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (agency_id, supplier_id, provider_account_id),
    FOREIGN KEY (agency_id) REFERENCES agencies (agency_id) ON DELETE RESTRICT,
    FOREIGN KEY (supplier_id, provider_account_id) 
        REFERENCES supplier_provider_accounts (supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS payment_provider_accounts (
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_global INTEGER NOT NULL DEFAULT 0 CHECK (is_global IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (payment_provider_id, provider_account_id)
);

CREATE TABLE IF NOT EXISTS agency_payment_account_authorizations (
    agency_id TEXT NOT NULL,
    payment_provider_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (agency_id, payment_provider_id, provider_account_id),
    FOREIGN KEY (agency_id) REFERENCES agencies (agency_id) ON DELETE RESTRICT,
    FOREIGN KEY (payment_provider_id, provider_account_id) 
        REFERENCES payment_provider_accounts (payment_provider_id, provider_account_id) ON DELETE RESTRICT
);

-- ============================================================================
-- 3. M2.3A PARENT STAY COVERAGE SPECIFICATIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS parent_stay_coverage_specs (
    coverage_spec_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL UNIQUE,
    agency_id TEXT NOT NULL,
    topology TEXT NOT NULL CHECK (topology IN ('SINGLE_ROOM', 'MULTI_ROOM', 'SPLIT_STAY')),
    hotel_id TEXT NOT NULL,
    check_in_date TEXT NOT NULL,
    check_out_date TEXT NOT NULL,
    expected_children_count INTEGER NOT NULL CHECK (expected_children_count BETWEEN 1 AND 2),
    total_rooms INTEGER NOT NULL CHECK (total_rooms BETWEEN 1 AND 2),
    total_guest_capacity INTEGER NOT NULL CHECK (total_guest_capacity > 0),
    per_slot_requirements_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (parent_booking_id, agency_id) 
        REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT,
    CHECK (check_out_date > check_in_date)
);

-- ============================================================================
-- 4. M2.3A CHILD SUPPLIER INTENTS & SEGMENT ALLOCATIONS
-- ============================================================================
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
        REFERENCES booking_records (booking_id, agency_id) ON DELETE RESTRICT,
    FOREIGN KEY (supplier_id, provider_account_id) 
        REFERENCES supplier_provider_accounts (supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_intents_composite 
ON child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id);

CREATE TABLE IF NOT EXISTS child_segment_specs (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    hotel_id TEXT NOT NULL,
    room_slot_index INTEGER NOT NULL CHECK (room_slot_index BETWEEN 0 AND 1),
    segment_check_in TEXT NOT NULL,
    segment_check_out TEXT NOT NULL,
    room_type_code TEXT NOT NULL,
    guest_capacity INTEGER NOT NULL CHECK (guest_capacity > 0),
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    CHECK (segment_check_out > segment_check_in)
);

CREATE TABLE IF NOT EXISTS child_intent_pricing_snapshots (
    child_pricing_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    agency_allocated_gross INTEGER NOT NULL CHECK (agency_allocated_gross > 0),
    supplier_net_cost INTEGER NOT NULL CHECK (supplier_net_cost > 0),
    allocated_markup INTEGER NOT NULL CHECK (allocated_markup >= 0),
    allocated_taxes INTEGER NOT NULL CHECK (allocated_taxes >= 0),
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    supplier_cancellation_penalty_bps INTEGER NOT NULL DEFAULT 0 CHECK (supplier_cancellation_penalty_bps BETWEEN 0 AND 10000),
    created_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    CHECK (agency_allocated_gross = supplier_net_cost + allocated_markup + allocated_taxes)
);

-- Bijection constraint: Exactly one pricing snapshot per child intent per parent booking
CREATE UNIQUE INDEX IF NOT EXISTS uidx_child_pricing_bijection 
ON child_intent_pricing_snapshots (parent_booking_id, child_intent_id);

-- Trigger to verify active account authorization on child intent creation
CREATE TRIGGER IF NOT EXISTS trg_check_supplier_account_authorization
BEFORE INSERT ON child_supplier_intents
BEGIN
    SELECT CASE
        WHEN (
            EXISTS (
                SELECT 1 FROM supplier_provider_accounts
                WHERE supplier_id = NEW.supplier_id
                  AND provider_account_id = NEW.provider_account_id
                  AND is_global = 1
                  AND is_active = 1
            )
            OR
            EXISTS (
                SELECT 1 FROM agency_provider_account_authorizations a
                JOIN supplier_provider_accounts s
                  ON s.supplier_id = a.supplier_id
                 AND s.provider_account_id = a.provider_account_id
                WHERE a.agency_id = NEW.agency_id
                  AND a.supplier_id = NEW.supplier_id
                  AND a.provider_account_id = NEW.provider_account_id
                  AND a.is_enabled = 1
                  AND s.is_active = 1
            )
        ) THEN 1
        ELSE RAISE(ABORT, 'AuthorizationError: Unauthorized or disabled supplier provider account for agency')
    END;
END;

-- ============================================================================
-- 5. M2.3A CHILD OPERATION OUTBOX (Disaggregated Leases & Nullable Idempotency Keys)
-- ============================================================================
CREATE TABLE IF NOT EXISTS child_operation_outbox (
    operation_id TEXT PRIMARY KEY,
    logical_operation_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'CREATE_RESERVATION', 'CANCEL_RESERVATION', 'LOOKUP_RESERVATION', 'LOOKUP_CANCELLATION'
    )),
    supplier_capability TEXT NOT NULL CHECK (supplier_capability IN (
        'IDEMPOTENT_WITH_LOOKUP', 'LOOKUP_ONLY', 'NEITHER'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED', 'EXHAUSTED', 'RECONCILIATION_REQUIRED'
    )),
    leased_by TEXT,
    lease_expires_at TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    supplier_idempotency_key TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    durable_outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    CHECK (
        (supplier_capability = 'IDEMPOTENT_WITH_LOOKUP' AND supplier_idempotency_key IS NOT NULL AND length(supplier_idempotency_key) > 0)
        OR
        (supplier_capability IN ('LOOKUP_ONLY', 'NEITHER') AND supplier_idempotency_key IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_outbox_op_attempt_type 
ON child_operation_outbox (
    operation_id, attempt_id, logical_operation_id, operation_type, 
    child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id
);

-- Trigger to verify active authorization at outbox dispatch creation time
CREATE TRIGGER IF NOT EXISTS trg_check_outbox_dispatch_authorization
BEFORE INSERT ON child_operation_outbox
BEGIN
    SELECT CASE
        WHEN (
            EXISTS (
                SELECT 1 FROM supplier_provider_accounts
                WHERE supplier_id = NEW.supplier_id
                  AND provider_account_id = NEW.provider_account_id
                  AND is_global = 1
                  AND is_active = 1
            )
            OR
            EXISTS (
                SELECT 1 FROM agency_provider_account_authorizations a
                JOIN supplier_provider_accounts s
                  ON s.supplier_id = a.supplier_id
                 AND s.provider_account_id = a.provider_account_id
                WHERE a.agency_id = NEW.agency_id
                  AND a.supplier_id = NEW.supplier_id
                  AND a.provider_account_id = NEW.provider_account_id
                  AND a.is_enabled = 1
                  AND s.is_active = 1
            )
        ) THEN 1
        ELSE RAISE(ABORT, 'AuthorizationError: Unauthorized or disabled supplier provider account at outbox dispatch')
    END;
END;

-- Trigger to verify active authorization when outbox operation transitions to IN_PROGRESS (dispatch)
CREATE TRIGGER IF NOT EXISTS trg_check_outbox_status_dispatch_authorization
BEFORE UPDATE OF status ON child_operation_outbox
WHEN NEW.status = 'IN_PROGRESS'
BEGIN
    SELECT CASE
        WHEN (
            EXISTS (
                SELECT 1 FROM supplier_provider_accounts
                WHERE supplier_id = NEW.supplier_id
                  AND provider_account_id = NEW.provider_account_id
                  AND is_global = 1
                  AND is_active = 1
            )
            OR
            EXISTS (
                SELECT 1 FROM agency_provider_account_authorizations a
                JOIN supplier_provider_accounts s
                  ON s.supplier_id = a.supplier_id
                 AND s.provider_account_id = a.provider_account_id
                WHERE a.agency_id = NEW.agency_id
                  AND a.supplier_id = NEW.supplier_id
                  AND a.provider_account_id = NEW.provider_account_id
                  AND a.is_enabled = 1
                  AND s.is_active = 1
            )
        ) THEN 1
        ELSE RAISE(ABORT, 'AuthorizationError: Supplier provider account authorization was revoked before outbox dispatch')
    END;
END;

-- ============================================================================
-- 6. M2.3A TRULY APPEND-ONLY OBSERVATIONS & IMMUTABLE VALIDATIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS supplier_reservation_observations (
    observation_id TEXT PRIMARY KEY,
    operation_id TEXT,
    attempt_id TEXT,
    logical_operation_id TEXT,
    operation_type TEXT NOT NULL CHECK (operation_type IN (
        'CREATE_RESERVATION', 'CANCEL_RESERVATION', 'LOOKUP_RESERVATION', 'LOOKUP_CANCELLATION'
    )),
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    lifecycle_outcome TEXT NOT NULL CHECK (lifecycle_outcome IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,
    confirmed_hotel_id TEXT,
    confirmed_check_in TEXT,
    confirmed_check_out TEXT,
    confirmed_room_type TEXT,
    confirmed_guest_capacity INTEGER CHECK (confirmed_guest_capacity IS NULL OR confirmed_guest_capacity > 0),
    worker_fencing_token INTEGER CHECK (worker_fencing_token IS NULL OR worker_fencing_token > 0),
    raw_response_hash TEXT NOT NULL,
    payload_snapshot TEXT NOT NULL,
    observation_source TEXT NOT NULL CHECK (observation_source IN (
        'WORKER_DISPATCH', 'LOOKUP_POLL', 'WEBHOOK_NOTIFICATION', 'MANUAL_RECONCILIATION'
    )),
    provenance_metadata_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES child_supplier_intents (child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT,
    FOREIGN KEY (
        operation_id, attempt_id, logical_operation_id, operation_type, 
        child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id
    ) REFERENCES child_operation_outbox (
        operation_id, attempt_id, logical_operation_id, operation_type, 
        child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id
    ) ON DELETE RESTRICT,
    CHECK (
        (observation_source IN ('WORKER_DISPATCH', 'LOOKUP_POLL') 
            AND operation_id IS NOT NULL 
            AND attempt_id IS NOT NULL 
            AND logical_operation_id IS NOT NULL 
            AND worker_fencing_token IS NOT NULL)
        OR
        (observation_source IN ('WEBHOOK_NOTIFICATION', 'MANUAL_RECONCILIATION') 
            AND operation_id IS NULL 
            AND attempt_id IS NULL 
            AND logical_operation_id IS NULL 
            AND worker_fencing_token IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_obs_val_identity 
ON supplier_reservation_observations (
    observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_obs_res_identity 
ON supplier_reservation_observations (
    observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
    provider_account_id, lifecycle_outcome, supplier_reservation_id
);

-- Append-only triggers using RAISE(ABORT, ...)
CREATE TRIGGER IF NOT EXISTS trg_prevent_update_observations
BEFORE UPDATE ON supplier_reservation_observations
BEGIN
    SELECT RAISE(ABORT, 'ImmutabilityViolation: UPDATE on supplier_reservation_observations is strictly prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_observations
BEFORE DELETE ON supplier_reservation_observations
BEGIN
    SELECT RAISE(ABORT, 'ImmutabilityViolation: DELETE on supplier_reservation_observations is strictly prohibited');
END;

-- Dedicated Immutable Validation Outcomes Table
CREATE TABLE IF NOT EXISTS supplier_observation_validations (
    validation_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL UNIQUE,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK (validation_status IN ('VALIDATED', 'REJECTED', 'INCONCLUSIVE')),
    validated_by TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    validation_details_json TEXT NOT NULL,
    FOREIGN KEY (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id)
        REFERENCES supplier_reservation_observations (observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_val_composite 
ON supplier_observation_validations (
    validation_id, observation_id, child_intent_id, parent_booking_id, agency_id, 
    supplier_id, provider_account_id, validation_status
);

CREATE TRIGGER IF NOT EXISTS trg_prevent_update_validations
BEFORE UPDATE ON supplier_observation_validations
BEGIN
    SELECT RAISE(ABORT, 'ImmutabilityViolation: UPDATE on supplier_observation_validations is strictly prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_validations
BEFORE DELETE ON supplier_observation_validations
BEGIN
    SELECT RAISE(ABORT, 'ImmutabilityViolation: DELETE on supplier_observation_validations is strictly prohibited');
END;

-- ============================================================================
-- 7. RESOLUTION HISTORY (Append-Only) & CURRENT RESOLUTION POINTER
-- ============================================================================
CREATE TABLE IF NOT EXISTS child_authoritative_resolution_history (
    resolution_history_id TEXT PRIMARY KEY,
    child_intent_id TEXT NOT NULL,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    validation_id TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK (validation_status = 'VALIDATED'),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    resolved_by TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    FOREIGN KEY (
        observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
        provider_account_id, lifecycle_status, supplier_reservation_id
    ) REFERENCES supplier_reservation_observations (
        observation_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
        provider_account_id, lifecycle_outcome, supplier_reservation_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        validation_id, observation_id, child_intent_id, parent_booking_id, agency_id, 
        supplier_id, provider_account_id, validation_status
    ) REFERENCES supplier_observation_validations (
        validation_id, observation_id, child_intent_id, parent_booking_id, agency_id, 
        supplier_id, provider_account_id, validation_status
    ) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_res_hist_composite 
ON child_authoritative_resolution_history (
    resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
    provider_account_id, observation_id, lifecycle_status, supplier_reservation_id
);

CREATE TRIGGER IF NOT EXISTS trg_prevent_update_res_history
BEFORE UPDATE ON child_authoritative_resolution_history
BEGIN
    SELECT RAISE(ABORT, 'ImmutabilityViolation: UPDATE on child_authoritative_resolution_history is strictly prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_delete_res_history
BEFORE DELETE ON child_authoritative_resolution_history
BEGIN
    SELECT RAISE(ABORT, 'ImmutabilityViolation: DELETE on child_authoritative_resolution_history is strictly prohibited');
END;

CREATE TABLE IF NOT EXISTS child_current_authoritative_resolution (
    child_intent_id TEXT PRIMARY KEY,
    parent_booking_id TEXT NOT NULL,
    agency_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    active_resolution_history_id TEXT NOT NULL,
    active_observation_id TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'RESERVATION_ACTIVE', 'RESERVATION_CANCELLED', 'DEFINITIVE_FAILURE', 
        'NOT_FOUND', 'PENDING_INCONCLUSIVE', 'AMBIGUOUS'
    )),
    supplier_reservation_id TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    FOREIGN KEY (
        active_resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
        provider_account_id, active_observation_id, lifecycle_status, supplier_reservation_id
    ) REFERENCES child_authoritative_resolution_history (
        resolution_history_id, child_intent_id, parent_booking_id, agency_id, supplier_id, 
        provider_account_id, observation_id, lifecycle_status, supplier_reservation_id
    ) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_current_active_res_unique 
ON child_current_authoritative_resolution (supplier_id, provider_account_id, supplier_reservation_id)
WHERE supplier_reservation_id IS NOT NULL AND lifecycle_status = 'RESERVATION_ACTIVE';

-- Trigger to prevent stale fencing token from overwriting active authoritative resolution
CREATE TRIGGER IF NOT EXISTS trg_prevent_stale_fencing_token_resolution
BEFORE UPDATE OF fencing_token ON child_current_authoritative_resolution
WHEN NEW.fencing_token < OLD.fencing_token
BEGIN
    SELECT RAISE(ABORT, 'ConcurrencyViolation: Stale fencing token cannot overwrite current authoritative resolution');
END;

-- ============================================================================
-- 8. M2.3A PARENT PAYMENT OUTBOX
-- ============================================================================
CREATE TABLE IF NOT EXISTS parent_payment_outbox (
    payment_operation_id TEXT PRIMARY KEY,
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
    FOREIGN KEY (payment_intent_id, parent_booking_id, agency_id, payment_provider_id, provider_account_id) 
        REFERENCES payment_intents (payment_intent_id, booking_id, agency_id, payment_provider_id, provider_account_id) ON DELETE RESTRICT,
    FOREIGN KEY (payment_provider_id, provider_account_id) 
        REFERENCES payment_provider_accounts (payment_provider_id, provider_account_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_outbox_gateway_idemp 
ON parent_payment_outbox (payment_provider_id, provider_account_id, gateway_idempotency_key);

-- Trigger to verify active authorization on payment outbox creation
CREATE TRIGGER IF NOT EXISTS trg_check_payment_account_authorization
BEFORE INSERT ON parent_payment_outbox
BEGIN
    SELECT CASE
        WHEN (
            EXISTS (
                SELECT 1 FROM payment_provider_accounts
                WHERE payment_provider_id = NEW.payment_provider_id
                  AND provider_account_id = NEW.provider_account_id
                  AND is_global = 1
                  AND is_active = 1
            )
            OR
            EXISTS (
                SELECT 1 FROM agency_payment_account_authorizations a
                JOIN payment_provider_accounts p
                  ON p.payment_provider_id = a.payment_provider_id
                 AND p.provider_account_id = a.provider_account_id
                WHERE a.agency_id = NEW.agency_id
                  AND a.payment_provider_id = NEW.payment_provider_id
                  AND a.provider_account_id = NEW.provider_account_id
                  AND a.is_enabled = 1
                  AND p.is_active = 1
            )
        ) THEN 1
        ELSE RAISE(ABORT, 'AuthorizationError: Unauthorized or disabled payment provider account for agency')
    END;
END;

-- Trigger to verify active authorization when payment outbox transitions to IN_PROGRESS (dispatch)
CREATE TRIGGER IF NOT EXISTS trg_check_payment_outbox_status_dispatch_authorization
BEFORE UPDATE OF status ON parent_payment_outbox
WHEN NEW.status = 'IN_PROGRESS'
BEGIN
    SELECT CASE
        WHEN (
            EXISTS (
                SELECT 1 FROM payment_provider_accounts
                WHERE payment_provider_id = NEW.payment_provider_id
                  AND provider_account_id = NEW.provider_account_id
                  AND is_global = 1
                  AND is_active = 1
            )
            OR
            EXISTS (
                SELECT 1 FROM agency_payment_account_authorizations a
                JOIN payment_provider_accounts p
                  ON p.payment_provider_id = a.payment_provider_id
                 AND p.provider_account_id = a.provider_account_id
                WHERE a.agency_id = NEW.agency_id
                  AND a.payment_provider_id = NEW.payment_provider_id
                  AND a.provider_account_id = NEW.provider_account_id
                  AND a.is_enabled = 1
                  AND p.is_active = 1
            )
        ) THEN 1
        ELSE RAISE(ABORT, 'AuthorizationError: Payment provider account authorization was revoked before outbox dispatch')
    END;
END;
