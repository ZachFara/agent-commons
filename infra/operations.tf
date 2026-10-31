# Operations (decision 0033): encrypted nightly backups, uptime and error alerts. All of it fits
# the free tiers at current volume. The backup workflow is .github/workflows/backup.yml.

resource "google_project_service" "operations" {
  for_each           = toset(["monitoring.googleapis.com", "storage.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

# Backups: age-encrypted pg_dump files. Keep 14 daily and 6 monthly copies (well under 5 GB).
resource "google_storage_bucket" "backups" {
  name                        = "${var.project_id}-backups"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age            = 14
      matches_prefix = ["daily/"]
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      age            = 186
      matches_prefix = ["monthly/"]
    }
    action {
      type = "Delete"
    }
  }

  # It holds the only copies older than Neon's 6-hour history.
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.operations]
}

# The nightly workflow's identity: reads production through a read-only role, writes and reads
# backups, and runs retention against production.
resource "google_service_account" "operations" {
  account_id   = "agent-commons-ops"
  display_name = "Agent Commons operations (GitHub Actions: backups, retention)"
}

resource "google_service_account_iam_member" "github_impersonates_operations" {
  service_account_id = google_service_account.operations.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_storage_bucket_iam_member" "operations_backups" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
  bucket   = google_storage_bucket.backups.name
  role     = each.value
  member   = "serviceAccount:${google_service_account.operations.email}"
}

# Its value (the `commons_backup` role's URL) is added by tools/backup_role.py, which creates
# the role in the database; Terraform only owns the secret and who may read it.
resource "google_secret_manager_secret" "backup_url" {
  secret_id = "agent-commons-production-backup-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "operations_reads_backup_url" {
  secret_id = google_secret_manager_secret.backup_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.operations.email}"
}

resource "google_secret_manager_secret_iam_member" "operations_reads_database_url" {
  secret_id = google_secret_manager_secret.database_url["production"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.operations.email}"
}

# Alerts go by email to var.alert_emails (kept in the gitignored terraform.tfvars).
resource "google_monitoring_notification_channel" "email" {
  for_each     = toset(var.alert_emails)
  display_name = "Agent Commons alerts: ${each.value}"
  type         = "email"
  labels = {
    email_address = each.value
  }
  depends_on = [google_project_service.operations]
}

locals {
  alert_channels  = [for channel in google_monitoring_notification_channel.email : channel.id]
  production_host = trimprefix(google_cloud_run_v2_service.server["production"].uri, "https://")
  runbook         = "Runbook: infra/README.md, section \"When an alert fires\"."
}

# /health never touches the database, so the check doesn't keep Neon awake.
resource "google_monitoring_uptime_check_config" "production" {
  display_name = "Agent Commons production /health"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = local.production_host
    }
  }
  content_matchers {
    content = "ok"
    matcher = "CONTAINS_STRING"
  }
  depends_on = [google_project_service.operations]
}

# Down for two checks in a row from more than one region. This also catches the console's
# Cloud Run spend cap pausing the service.
resource "google_monitoring_alert_policy" "production_down" {
  display_name          = "Agent Commons production is down"
  combiner              = "OR"
  notification_channels = local.alert_channels

  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.production.uptime_check_id}\""
      duration        = "600s"
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }
  documentation {
    content   = local.runbook
    mime_type = "text/markdown"
  }
}

# Server errors: 5xx responses, and call-log lines with severity ERROR (decision 0024).
resource "google_logging_metric" "production_errors" {
  name   = "agent-commons-production-errors"
  filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${google_cloud_run_v2_service.server["production"].name}\" AND (severity>=ERROR OR httpRequest.status>=500)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "production_errors" {
  display_name          = "Agent Commons production errors"
  combiner              = "OR"
  notification_channels = local.alert_channels

  conditions {
    display_name = "5 or more server errors in 10 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.production_errors.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 4
      aggregations {
        alignment_period     = "600s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
      trigger {
        count = 1
      }
    }
  }
  alert_strategy {
    auto_close = "1800s"
  }
  documentation {
    content   = local.runbook
    mime_type = "text/markdown"
  }
}
