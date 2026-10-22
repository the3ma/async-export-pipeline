-- Deterministic demo data for activity_event (psql script).
--
--   psql -v rows=1200000 -v seed_end=2026-09-30 -f db/002_seed.sql
--
-- Variables (both optional):
--   rows      number of events to generate               (default 1200000)
--   seed_end  last day covered; data spans 18 months back (default 2026-09-30)
--
-- Every value is derived from the row number through hashint8extended(n, salt), never random(),
-- so the same parameters always produce the same rows (and the same event_ids). The script only
-- seeds an empty table, so re-running it is a no-op.
--
-- Distribution (roughly): orders 35 %, billing 25 %, catalog 20 %, accounts 12 %, settings 8 %;
-- volume grows over time; 75 % of events fall in business hours (08:00-18:00 UTC); a few heavy
-- actors and "hot" entities (squared uniform draws) like a real product.

\if :{?rows}
\else
    \set rows 1200000
\endif
\if :{?seed_end}
\else
    \set seed_end 2026-09-30
\endif

\echo 'seeding' :rows 'activity events ending' :seed_end

\timing on

INSERT INTO activity_event (occurred_at, actor_id, area, action, entity_type, entity_id, details)
SELECT
    occurred_at, actor_id, area, action, entity_type,
    entity_type || '_' || lpad(entity_no::text, 6, '0') AS entity_id,
    details
FROM (
    SELECT
        n,
        -- Day grows as position^0.8 => more events per day towards the end (product growth).
        date_trunc('day', w.window_start
                + power((n - 1)::float8 / :rows, 0.8) * (w.window_end - w.window_start), 'UTC')
            + CASE WHEN u_hour < 0.75
                   THEN interval '8 hours' + u_time * interval '10 hours'   -- business hours
                   ELSE u_time * interval '24 hours'
              END AS occurred_at,
        'usr_' || lpad(floor(power(u_actor, 2) * 5000)::int::text, 4, '0') AS actor_id,
        area,
        action,
        CASE WHEN action = 'sign_in' THEN 'session'
             ELSE (CASE area
                       WHEN 'accounts' THEN ARRAY['user', 'role', 'api_key']
                       WHEN 'billing'  THEN ARRAY['invoice', 'payment', 'subscription']
                       WHEN 'catalog'  THEN ARRAY['product', 'category', 'price_list']
                       WHEN 'orders'   THEN ARRAY['order', 'shipment', 'refund']
                       ELSE                 ARRAY['preference', 'integration', 'webhook']
                   END)[1 + floor(u_entity * 3)::int]
        END AS entity_type,
        floor(power(u_entity_no, 2) * 200000)::int AS entity_no,
        CASE action
            WHEN 'update' THEN CASE
                WHEN u_field < 0.6 THEN jsonb_build_array(jsonb_build_object(
                    'field', 'status',
                    'old',   (ARRAY['draft', 'active', 'paused', 'review'])[1 + floor(u_old * 4)::int],
                    'new',   (ARRAY['active', 'paused', 'archived', 'approved'])[1 + floor(u_new * 4)::int]))
                WHEN u_field < 0.85 THEN jsonb_build_array(jsonb_build_object(
                    'field', 'priority',
                    'old',   (ARRAY['low', 'normal', 'high'])[1 + floor(u_old * 3)::int],
                    'new',   (ARRAY['low', 'normal', 'high', 'urgent'])[1 + floor(u_new * 4)::int]))
                ELSE jsonb_build_array(jsonb_build_object(
                    'field', 'owner_id',
                    'old',   'usr_' || lpad(floor(u_old * 5000)::int::text, 4, '0'),
                    'new',   'usr_' || lpad(floor(u_new * 5000)::int::text, 4, '0')))
            END
            WHEN 'create' THEN jsonb_build_array(jsonb_build_object(
                'field', 'status', 'old', NULL,
                'new',   (ARRAY['draft', 'active'])[1 + floor(u_new * 2)::int]))
            WHEN 'delete' THEN jsonb_build_array(jsonb_build_object(
                'field', 'status',
                'old',   (ARRAY['active', 'paused', 'archived'])[1 + floor(u_old * 3)::int],
                'new',   NULL))
            ELSE '[]'::jsonb
        END AS details
    FROM (
        SELECT
            n, u_hour, u_time, u_actor, u_entity, u_entity_no, u_field, u_old, u_new,
            area,
            CASE
                WHEN area = 'accounts' AND u_action < 0.45 THEN 'sign_in'
                WHEN u_action2 < 0.40 THEN 'view'
                WHEN u_action2 < 0.65 THEN 'update'
                WHEN u_action2 < 0.83 THEN 'create'
                WHEN u_action2 < 0.93 THEN 'export'
                ELSE 'delete'
            END AS action
        FROM (
            SELECT
                n,
                CASE
                    WHEN u_area < 0.35 THEN 'orders'
                    WHEN u_area < 0.60 THEN 'billing'
                    WHEN u_area < 0.80 THEN 'catalog'
                    WHEN u_area < 0.92 THEN 'accounts'
                    ELSE 'settings'
                END AS area,
                u_action, u_action2, u_hour, u_time, u_actor, u_entity, u_entity_no,
                u_field, u_old, u_new
            FROM (
                -- Uniform [0, 1) draws, one independent salt per attribute.
                SELECT
                    n,
                    (hashint8extended(n, 1) & 2147483647)::float8 / 2147483648 AS u_area,
                    (hashint8extended(n, 2) & 2147483647)::float8 / 2147483648 AS u_action,
                    (hashint8extended(n, 3) & 2147483647)::float8 / 2147483648 AS u_action2,
                    (hashint8extended(n, 4) & 2147483647)::float8 / 2147483648 AS u_hour,
                    (hashint8extended(n, 5) & 2147483647)::float8 / 2147483648 AS u_time,
                    (hashint8extended(n, 6) & 2147483647)::float8 / 2147483648 AS u_actor,
                    (hashint8extended(n, 7) & 2147483647)::float8 / 2147483648 AS u_entity,
                    (hashint8extended(n, 8) & 2147483647)::float8 / 2147483648 AS u_entity_no,
                    (hashint8extended(n, 9) & 2147483647)::float8 / 2147483648 AS u_field,
                    (hashint8extended(n, 10) & 2147483647)::float8 / 2147483648 AS u_old,
                    (hashint8extended(n, 11) & 2147483647)::float8 / 2147483648 AS u_new
                FROM generate_series(1, :rows) AS n
            ) AS draws
        ) AS with_area
    ) AS with_action
    CROSS JOIN (
        -- [seed_end + 1 day - 18 months, seed_end + 1 day) in UTC, whatever the session TimeZone.
        SELECT ((date :'seed_end' + 1) - interval '18 months') AT TIME ZONE 'UTC' AS window_start,
               (date :'seed_end' + 1)::timestamp AT TIME ZONE 'UTC'               AS window_end
    ) AS w
) AS generated
WHERE NOT EXISTS (SELECT 1 FROM activity_event)
-- Time order == id order (within a day the time of day is shuffled, like real traffic).
ORDER BY n;

\timing off

ANALYZE activity_event;

SELECT area, count(*) AS events, min(occurred_at) AS first_event, max(occurred_at) AS last_event
FROM activity_event
GROUP BY area
ORDER BY events DESC;
