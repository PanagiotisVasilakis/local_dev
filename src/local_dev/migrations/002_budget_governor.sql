CREATE TABLE budget_periods (
    period_utc TEXT PRIMARY KEY,
    limit_micros INTEGER NOT NULL CHECK (limit_micros > 0),
    created_at TEXT NOT NULL
);

CREATE TABLE budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    period_utc TEXT NOT NULL REFERENCES budget_periods(period_utc),
    task_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    reserved_micros INTEGER NOT NULL CHECK (reserved_micros > 0),
    actual_micros INTEGER CHECK (actual_micros IS NULL OR actual_micros >= 0),
    status TEXT NOT NULL CHECK (status IN ('active','settled','cancelled','breached')),
    created_at TEXT NOT NULL,
    settled_at TEXT,
    cancelled_at TEXT,
    breach_reason TEXT,
    CHECK (
        (status = 'active' AND actual_micros IS NULL AND settled_at IS NULL
            AND cancelled_at IS NULL AND breach_reason IS NULL)
        OR
        (status = 'settled' AND actual_micros IS NOT NULL
            AND actual_micros <= reserved_micros AND settled_at IS NOT NULL
            AND cancelled_at IS NULL AND breach_reason IS NULL)
        OR
        (status = 'cancelled' AND actual_micros IS NULL AND settled_at IS NULL
            AND cancelled_at IS NOT NULL AND breach_reason IS NULL)
        OR
        (status = 'breached' AND actual_micros IS NOT NULL
            AND actual_micros > reserved_micros AND settled_at IS NOT NULL
            AND cancelled_at IS NULL AND breach_reason IS NOT NULL)
    )
);

CREATE INDEX budget_reservations_period_status_idx
ON budget_reservations(period_utc, status);

CREATE TRIGGER budget_periods_no_delete
BEFORE DELETE ON budget_periods
BEGIN
    SELECT RAISE(ABORT, 'budget periods are immutable');
END;

CREATE TRIGGER budget_periods_limit_immutable
BEFORE UPDATE OF period_utc, limit_micros, created_at ON budget_periods
BEGIN
    SELECT RAISE(ABORT, 'budget period policy is immutable');
END;

CREATE TRIGGER budget_reservations_no_delete
BEFORE DELETE ON budget_reservations
BEGIN
    SELECT RAISE(ABORT, 'budget reservations are immutable ledger entries');
END;

CREATE TRIGGER budget_reservations_identity_immutable
BEFORE UPDATE OF
    reservation_id, idempotency_key, period_utc, task_id, provider, model,
    reserved_micros, created_at
ON budget_reservations
BEGIN
    SELECT RAISE(ABORT, 'budget reservation identity is immutable');
END;
