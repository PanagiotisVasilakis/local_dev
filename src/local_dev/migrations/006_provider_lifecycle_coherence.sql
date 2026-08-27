CREATE TABLE _migration_006_provider_coherence_guard (
    violations INTEGER NOT NULL CHECK (violations = 0)
);

INSERT INTO _migration_006_provider_coherence_guard(violations)
SELECT COUNT(*)
FROM provider_calls AS pc
LEFT JOIN budget_reservations AS br
    ON br.reservation_id = pc.reservation_id
WHERE br.reservation_id IS NULL
   OR pc.idempotency_key != br.idempotency_key
   OR pc.provider != br.provider
   OR pc.model != br.model
   OR (pc.status IN ('prepared','uncertain') AND br.status != 'active')
   OR (pc.status = 'dispatching'
       AND br.status NOT IN ('active','settled','cancelled','breached'))
   OR (pc.status = 'completed' AND br.status NOT IN ('settled','breached'))
   OR (pc.status = 'not_sent' AND br.status != 'cancelled');

DROP TABLE _migration_006_provider_coherence_guard;

CREATE TRIGGER provider_calls_budget_state_coherence
BEFORE UPDATE OF status ON provider_calls
WHEN (
    NEW.status IN ('prepared','dispatching','uncertain')
    AND NOT EXISTS (
        SELECT 1 FROM budget_reservations
        WHERE reservation_id = NEW.reservation_id AND status = 'active'
    )
) OR (
    NEW.status = 'completed'
    AND NOT EXISTS (
        SELECT 1 FROM budget_reservations
        WHERE reservation_id = NEW.reservation_id AND status IN ('settled','breached')
    )
) OR (
    NEW.status = 'not_sent'
    AND NOT EXISTS (
        SELECT 1 FROM budget_reservations
        WHERE reservation_id = NEW.reservation_id AND status = 'cancelled'
    )
)
BEGIN
    SELECT RAISE(ABORT, 'provider call status conflicts with budget reservation state');
END;

CREATE TRIGGER budget_reservations_provider_state_coherence
BEFORE UPDATE OF status ON budget_reservations
WHEN OLD.status = 'active'
 AND NEW.status IN ('settled','cancelled','breached')
 AND EXISTS (
    SELECT 1 FROM provider_calls WHERE reservation_id = NEW.reservation_id
 )
 AND NOT EXISTS (
    SELECT 1 FROM provider_calls
    WHERE reservation_id = NEW.reservation_id AND status = 'dispatching'
 )
BEGIN
    SELECT RAISE(ABORT, 'budget reservation transition conflicts with provider call state');
END;
