CREATE TABLE provider_calls (
    call_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    reservation_id TEXT NOT NULL UNIQUE
        REFERENCES budget_reservations(reservation_id),
    request_fingerprint TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('prepared','dispatching','completed','not_sent','uncertain')),
    created_at TEXT NOT NULL,
    dispatch_started_at TEXT,
    terminal_at TEXT,
    error_type TEXT,
    CHECK (
        (status = 'prepared'
            AND dispatch_started_at IS NULL AND terminal_at IS NULL AND error_type IS NULL)
        OR
        (status = 'dispatching'
            AND dispatch_started_at IS NOT NULL AND terminal_at IS NULL AND error_type IS NULL)
        OR
        (status = 'completed'
            AND dispatch_started_at IS NOT NULL AND terminal_at IS NOT NULL
            AND error_type IS NULL)
        OR
        (status IN ('not_sent','uncertain')
            AND dispatch_started_at IS NOT NULL AND terminal_at IS NOT NULL
            AND error_type IS NOT NULL)
    )
);

CREATE INDEX provider_calls_status_idx ON provider_calls(status);

CREATE TRIGGER provider_calls_no_delete
BEFORE DELETE ON provider_calls
BEGIN
    SELECT RAISE(ABORT, 'provider call ledger entries are immutable');
END;

CREATE TRIGGER provider_calls_identity_immutable
BEFORE UPDATE OF
    call_id, idempotency_key, reservation_id, request_fingerprint,
    provider, model, created_at
ON provider_calls
BEGIN
    SELECT RAISE(ABORT, 'provider call identity is immutable');
END;

CREATE TRIGGER provider_calls_transition_guard
BEFORE UPDATE ON provider_calls
WHEN NOT (
    (OLD.status = 'prepared' AND NEW.status = 'dispatching')
    OR
    (OLD.status = 'dispatching'
        AND NEW.status IN ('completed','not_sent','uncertain'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid provider call state transition');
END;
