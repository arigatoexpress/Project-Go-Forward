-- THO daily lead-response SLA check (COD-254).
-- Runs as a BigQuery scheduled query at 6:45 AM MT, 30 min after tho-leads-bq-daily
-- refreshes tho_analytics.leads. It keeps one PII-free row per run in lead_sla_daily.
-- When any reachable lead still has no first contact after 2 hours, it calls ERROR().
-- The failed run writes an ERROR log line that contains LEAD_SLA_BREACH, and the
-- log-match alert policy in alert_policy.json emails the owner through the existing
-- "THO operator email" channel. No names, emails or phone numbers are read or written.
DECLARE breach_count INT64;
DECLARE unassigned_count INT64;
DECLARE over_24h_count INT64;
DECLARE oldest_hours INT64;
DECLARE mirror_age_hours INT64;

CREATE TABLE IF NOT EXISTS `tho-ai-agent.tho_analytics.lead_sla_daily` (
  checked_at TIMESTAMP NOT NULL,
  waiting_over_2h INT64,
  waiting_over_24h INT64,
  unassigned_waiting INT64,
  oldest_wait_hours INT64,
  mirror_age_hours INT64
)
PARTITION BY DATE(checked_at)
OPTIONS (partition_expiration_days = 400, description = 'COD-254 daily lead-response SLA snapshot (PII-free)');

SET (breach_count, unassigned_count, over_24h_count, oldest_hours, mirror_age_hours) = (
  SELECT AS STRUCT
    COUNTIF(waiting),
    COUNTIF(waiting AND (assigned_to IS NULL OR TRIM(assigned_to) = '')),
    COUNTIF(waiting AND created_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)),
    IFNULL(MAX(IF(waiting, TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, HOUR), NULL)), 0),
    IFNULL(TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(updated_at), HOUR), 9999)
  FROM (
    SELECT
      assigned_to,
      created_at,
      updated_at,
      (has_phone OR has_email)
        AND status = 'new'
        AND first_contacted_at IS NULL
        AND created_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR) AS waiting
    FROM `tho-ai-agent.tho_analytics.leads`
  )
);

INSERT INTO `tho-ai-agent.tho_analytics.lead_sla_daily`
  (checked_at, waiting_over_2h, waiting_over_24h, unassigned_waiting, oldest_wait_hours, mirror_age_hours)
VALUES (CURRENT_TIMESTAMP(), breach_count, over_24h_count, unassigned_count, oldest_hours, mirror_age_hours);

IF breach_count > 0 THEN
  SELECT ERROR(FORMAT(
    'LEAD_SLA_BREACH: %d reachable lead(s) have had no first contact for over 2 hours (%d over 24 hours, %d unassigned, oldest %d hours). Staff: https://www.texashomeoutlet.com/staff -> Answer a lead.',
    breach_count, over_24h_count, unassigned_count, oldest_hours));
ELSEIF mirror_age_hours > 30 THEN
  SELECT ERROR(FORMAT(
    'LEAD_SLA_BREACH: lead mirror is %d hours old, so the SLA check cannot see new leads. Check the tho-leads-bq job.',
    mirror_age_hours));
END IF;
