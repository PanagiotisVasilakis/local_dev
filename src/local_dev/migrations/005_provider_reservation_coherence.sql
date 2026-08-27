CREATE TRIGGER provider_calls_reservation_coherence
BEFORE INSERT ON provider_calls
WHEN NOT EXISTS (
    SELECT 1
    FROM budget_reservations
    WHERE reservation_id = NEW.reservation_id
      AND idempotency_key = NEW.idempotency_key
      AND provider = NEW.provider
      AND model = NEW.model
      AND status = 'active'
)
BEGIN
    SELECT RAISE(ABORT, 'provider call must match an active budget reservation');
END;
