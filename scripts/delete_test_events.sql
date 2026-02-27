-- Delete test events and all related data
-- Usage: Set the event IDs below, then run against the database
--   psql $DATABASE_URL -f scripts/delete_test_events.sql
--
-- All child tables use CASCADE on delete except platform_invoices (RESTRICT),
-- so we only need to handle invoices explicitly before deleting events.

BEGIN;

-- ============================================
-- SET TARGET EVENT IDS HERE
-- ============================================
CREATE TEMP TABLE target_events (id INT PRIMARY KEY);
INSERT INTO target_events (id) VALUES
    (236),
    (237),
    (239),
    (240)
;

-- Bail out if no events specified
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM target_events) THEN
        RAISE EXCEPTION 'No event IDs specified — edit the INSERT INTO target_events block';
    END IF;
END $$;

-- ============================================
-- PRE-DELETE SUMMARY (dry-run info)
-- ============================================
SELECT '=== Events to delete ===' AS info;
SELECT e.id, e.name, e.event_type, e.status, e.created_at
FROM events e
JOIN target_events t ON e.id = t.id
ORDER BY e.id;

SELECT '=== Row counts ===' AS info;

SELECT 'event_enrollments' AS table_name, COUNT(*) AS rows
FROM event_enrollments WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'catches', COUNT(*)
FROM catches WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_scoreboards', COUNT(*)
FROM event_scoreboards WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ranking_movements', COUNT(*)
FROM ranking_movements WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_game_cards', COUNT(*)
FROM ta_game_cards WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_matches', COUNT(*)
FROM ta_matches WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_lineups', COUNT(*)
FROM ta_lineups WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_knockout_brackets', COUNT(*)
FROM ta_knockout_brackets WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_knockout_matches', COUNT(*)
FROM ta_knockout_matches WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_qualifier_standings', COUNT(*)
FROM ta_qualifier_standings WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_event_settings', COUNT(*)
FROM ta_event_settings WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'ta_event_point_configs', COUNT(*)
FROM ta_event_point_configs WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'platform_invoices', COUNT(*)
FROM platform_invoices WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_chat_messages', COUNT(*)
FROM event_chat_messages WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_validators', COUNT(*)
FROM event_validators WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_sponsors', COUNT(*)
FROM event_sponsors WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_bans', COUNT(*)
FROM event_bans WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_contestations', COUNT(*)
FROM event_contestations WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'route_histories', COUNT(*)
FROM route_histories WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'organizer_messages', COUNT(*)
FROM organizer_messages WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_prizes', COUNT(*)
FROM event_prizes WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_fish_scoring', COUNT(*)
FROM event_fish_scoring WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_species_bonus_points', COUNT(*)
FROM event_species_bonus_points WHERE event_id IN (SELECT id FROM target_events)
UNION ALL
SELECT 'event_scoring_rules', COUNT(*)
FROM event_scoring_rules WHERE event_id IN (SELECT id FROM target_events)
ORDER BY table_name;

-- ============================================
-- DELETE
-- ============================================

-- 1. Handle platform_invoices (RESTRICT constraint — must delete first)
DELETE FROM platform_invoices
WHERE event_id IN (SELECT id FROM target_events);

-- 2. Delete events — CASCADE handles all other child tables
DELETE FROM events
WHERE id IN (SELECT id FROM target_events);

SELECT '=== Deleted ===' AS info;
SELECT COUNT(*) AS events_deleted FROM target_events;

DROP TABLE target_events;

COMMIT;
