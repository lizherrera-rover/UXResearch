/* ============================================================
   ROVER RESEARCH OPS — Interview Candidate Pool
   ============================================================
   Purpose : Pull a pool of interviewable owners and sitters.
             Unlike the survey checker, this has NO 90-day
             email throttle. All country/segment/user_type
             filters are applied in the Streamlit app — this
             query returns the full eligible pool.

   Output columns
   ──────────────
   person_id, first_name, email, user_type, country_code,
   had_conversation_L12, had_booking_L12,
   -- sitter-only --
   segment, grouped_segment, provider_stage,
   primary_service_location, primary_service_time,
   offers_boarding, offers_house_sitting, offers_drop_ins,
   offers_dog_walking, offers_day_care,
   -- owner-only --
   total_bookings_L12, most_recent_booked_conversation

   How to use
   ──────────
   1. Run in Mode
   2. Export CSV
   3. Upload to the Streamlit interview scheduling app
   ============================================================ */

WITH all_users AS (
    SELECT DISTINCT
        pp.id                                                          AS person_id
        , au.first_name
        , au.email
        , ut.email_permission_status
        , pp.is_sensitive
        , CASE WHEN sitter_intent = 1 THEN 'sitter' ELSE 'owner' END  AS user_type
        , pp.added                                                     AS pp_added
        , plw.country_code
    FROM roverdb.people_person pp
    LEFT JOIN roverdb.auth_user au
           ON pp.user_id = au.id
    LEFT JOIN email_marketing.users_timely ut
           ON au.email = ut.email
    LEFT JOIN standard_reports.person_location_windows plw
           ON plw.window_end IS NULL
          AND plw.person_id = pp.id
    LEFT JOIN standard_reports.sitter_intent si
           ON si.person_id = pp.id
    WHERE email_permission_status = 'subscribed'
      AND plw.country_code IS NOT NULL
      -- exclude email holdout groups (same logic as survey checker)
      AND (
          (sitter_intent = 1 AND MOD(pp.id, 10) <> 3)
          OR (sitter_intent = 0 AND MOD(pp.id, 10) <> 7)
      )
)

, active_users AS (
    -- owner activity
    SELECT
        requester_id                                                            AS person_id
        , 'owner'::VARCHAR                                                      AS user_type
        , MAX(conversation_added)                                               AS most_recent_conversation
        , MAX(CASE WHEN has_stay = 1 THEN conversation_added ELSE NULL END)     AS most_recent_booked_conversation
        , COUNT(CASE WHEN has_stay = 1
                      AND conversation_added >= CURRENT_DATE - 365
                     THEN 1 END)                                                AS total_bookings_L12
    FROM standard_reports.conversations
    GROUP BY 1, 2

    UNION ALL

    -- sitter activity
    SELECT
        provider_id                                                             AS person_id
        , 'sitter'::VARCHAR                                                     AS user_type
        , MAX(conversation_added)                                               AS most_recent_conversation
        , MAX(CASE WHEN has_stay = 1 THEN conversation_added ELSE NULL END)     AS most_recent_booked_conversation
        , COUNT(CASE WHEN has_stay = 1
                      AND conversation_added >= CURRENT_DATE - 365
                     THEN 1 END)                                                AS total_bookings_L12
    FROM standard_reports.conversations
    GROUP BY 1, 2
)

, memorialized_pets AS (
    SELECT
        owner_id
        , MAX(is_memorialized) AS has_memorialized_pet
    FROM standard_reports.pets
    GROUP BY 1
)

, most_recent_email_click AS (
    SELECT email, MAX(click_timestamp) AS most_recent_email_click
    FROM email_marketing.clicks
    GROUP BY 1
)

, most_recent_email_open AS (
    SELECT email, MAX(open_timestamp) AS most_recent_email_open
    FROM email_marketing.opens
    GROUP BY 1
)

-- ── Base eligible pool ─────────────────────────────────────────────────────

, eligible_pool AS (
    SELECT
        au.person_id
        , au.first_name
        , au.email
        , au.user_type
        , au.pp_added
        , au.country_code
        , cl.most_recent_email_click
        , op.most_recent_email_open
        , a.most_recent_conversation
        , a.most_recent_booked_conversation
        , a.total_bookings_L12
        , CASE WHEN a.most_recent_conversation > CURRENT_DATE - 365
               THEN 1 ELSE 0 END                                       AS had_conversation_L12
        , CASE WHEN a.most_recent_booked_conversation > CURRENT_DATE - 365
               THEN 1 ELSE 0 END                                       AS had_booking_L12
    FROM all_users au
    LEFT JOIN most_recent_email_click cl ON cl.email = au.email
    LEFT JOIN most_recent_email_open  op ON op.email = au.email
    LEFT JOIN active_users a             ON a.person_id = au.person_id
                                       AND a.user_type = au.user_type
    LEFT JOIN memorialized_pets m        ON m.owner_id = au.person_id
    WHERE is_sensitive = 0
      AND COALESCE(has_memorialized_pet, 0) = 0
      -- engagement filter: active email user OR new account (same as survey checker)
      AND (
          au.pp_added >= CURRENT_DATE - 90
          OR op.most_recent_email_open  >= CURRENT_DATE - 180
          OR cl.most_recent_email_click >= CURRENT_DATE - 180
      )
)

-- ── Sitter-specific attributes ─────────────────────────────────────────────

, sitter_details AS (
    SELECT
        ep.person_id
        , MAX(COALESCE(ps.mece_segmentation, 'no-segment'))            AS segment
        , CASE
            WHEN MAX(ps.mece_segmentation) LIKE '%HVS%'        THEN 'hvs'
            WHEN MAX(ps.mece_segmentation) LIKE '%consistent%' THEN 'consistent'
            WHEN MAX(ps.mece_segmentation) LIKE '%infrequent%' THEN 'infrequent'
            WHEN MAX(ps.mece_segmentation) LIKE '%absent%'     THEN 'churned'
            WHEN MAX(ps.mece_segmentation) LIKE '%fail%'       THEN 'stalled'
            WHEN MAX(ps.mece_segmentation) = 'no-segment'      THEN 'no-segment'
            ELSE 'new'
          END                                                          AS grouped_segment
        , MAX(COALESCE(ps.primary_service_location, 'none'))           AS primary_service_location
        , MAX(COALESCE(ps.primary_service_time, 'none'))               AS primary_service_time
        , MAX(CASE WHEN sst.slug = 'overnight-boarding'   THEN 1 ELSE 0 END) AS offers_boarding
        , MAX(CASE WHEN sst.slug = 'overnight-traveling'  THEN 1 ELSE 0 END) AS offers_house_sitting
        , MAX(CASE WHEN sst.slug = 'drop-in'              THEN 1 ELSE 0 END) AS offers_drop_ins
        , MAX(CASE WHEN sst.slug = 'dog-walking'          THEN 1 ELSE 0 END) AS offers_dog_walking
        , MAX(CASE WHEN sst.slug = 'doggy-day-care'       THEN 1 ELSE 0 END) AS offers_day_care
    FROM eligible_pool ep
    LEFT JOIN standard_reports.provider_segmentation ps
           ON ps.provider_id = ep.person_id
          AND ps.record_month = DATE_TRUNC('month', CURRENT_DATE) -- always current month
    LEFT JOIN roverdb.services_service ss
           ON ss.provider_id = ep.person_id
    JOIN  roverdb.services_servicetype sst
           ON sst.id = ss.service_type_id
    WHERE ep.user_type = 'sitter'
    GROUP BY 1
)

-- ── UX Research incentives (staff deposits / credits) ─────────────────────

, ux_incentives AS (
    SELECT
        pw.person_id
        , COUNT(sd.id)    AS total_ux_incentives
        , MAX(sd.added)   AS most_recent_ux_incentive  -- 'added' is Django's auto_now_add field
    FROM roverdb.payments_wallet pw
    JOIN roverdb.payments_staffdeposit sd ON sd.wallet_id = pw.id
    GROUP BY 1
)

, sitter_stage AS (
    SELECT
        provider_id
        , CASE
            WHEN MAX(months_since_matriculated) IS NOT NULL THEN 'matriculated'
            WHEN MAX(months_since_activated)    IS NOT NULL THEN 'activated'
            WHEN MAX(months_since_searchable)   IS NOT NULL THEN 'searchable'
            ELSE NULL
          END AS provider_stage
    FROM standard_reports.provider_months
    GROUP BY 1
)

-- ── Final output ───────────────────────────────────────────────────────────

SELECT
    ep.person_id
    , ep.first_name
    , ep.email
    , ep.user_type
    , ep.country_code
    , ep.had_conversation_L12
    , ep.had_booking_L12
    , ep.total_bookings_L12
    , ep.most_recent_booked_conversation
    -- sitter fields (NULL for owners)
    , sd.segment
    , sd.grouped_segment
    , ss.provider_stage
    , sd.primary_service_location
    , sd.primary_service_time
    , sd.offers_boarding
    , sd.offers_house_sitting
    , sd.offers_drop_ins
    , sd.offers_dog_walking
    , sd.offers_day_care
    -- UX incentive history (both user types)
    , COALESCE(ui.total_ux_incentives, 0)  AS total_ux_incentives
    , ui.most_recent_ux_incentive
    , CASE WHEN ui.total_ux_incentives > 0 THEN 1 ELSE 0 END AS has_received_incentive
FROM eligible_pool ep
LEFT JOIN sitter_details sd ON sd.person_id = ep.person_id
LEFT JOIN sitter_stage   ss ON ss.provider_id = ep.person_id
LEFT JOIN ux_incentives  ui ON ui.person_id = ep.person_id
ORDER BY ep.person_id  -- stable sort; randomization happens in the Streamlit app
;
