CREATE TRIGGER budget_reservations_terminal_immutable
BEFORE UPDATE ON budget_reservations
WHEN OLD.status != 'active'
BEGIN
    SELECT RAISE(ABORT, 'terminal budget reservations are immutable');
END;
