-- Delete test events and all related data
-- Usage: Set the event IDs below, then run against the database
--   psql $DATABASE_URL -f scripts/delete_test_events.sql
--
-- CASCADE handles all child tables except platform_invoices (RESTRICT).

BEGIN;

CREATE TEMP TABLE target_events (id INT PRIMARY KEY);
INSERT INTO target_events (id) VALUES
    (236),
    (237),
    (239),
    (240),
    (241),
    (242)
;

-- Show what we're about to delete
SELECT e.id, e.name, e.status, e.created_at
FROM events e JOIN target_events t ON e.id = t.id
ORDER BY e.id;

-- Delete invoices first (RESTRICT constraint), then events (CASCADE does the rest)
DELETE FROM platform_invoices WHERE event_id IN (SELECT id FROM target_events);
DELETE FROM events WHERE id IN (SELECT id FROM target_events);

SELECT COUNT(*) AS events_deleted FROM target_events;

DROP TABLE target_events;
COMMIT;
