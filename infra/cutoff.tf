# Billing cutoff (decision 0038): when this project's spend this month reaches var.cutoff_usd,
# a function unlinks billing from the project, which stops every Google Cloud service in it.
# Cloud Billing emails var.alert_emails at 50%, 90% and 100% of the cutoff. Needs
# var.billing_account; skipped when it is empty. Re-linking is in infra/README.md.

locals {
  cutoff_enabled = var.billing_account != ""
  cutoff_apis = local.cutoff_enabled ? toset([
    "cloudbilling.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
  ]) : toset([])
}

resource "google_project_service" "cutoff" {
  for_each           = local.cutoff_apis
  service            = each.value
  disable_on_destroy = false
}

resource "google_pubsub_topic" "billing_cutoff" {
  count      = local.cutoff_enabled ? 1 : 0
  name       = "agent-commons-billing-cutoff"
  depends_on = [google_project_service.cutoff]
}

resource "google_billing_budget" "cutoff" {
  provider        = google.billing
  count           = local.cutoff_enabled ? 1 : 0
  billing_account = var.billing_account
  display_name    = "Agent Commons billing cutoff"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.cutoff_usd)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  all_updates_rule {
    pubsub_topic                     = google_pubsub_topic.billing_cutoff[0].id
    schema_version                   = "1.0"
    monitoring_notification_channels = local.alert_channels
  }
  depends_on = [google_project_service.billing_budgets]
}

# The function's identity: it may only link and unlink this project's billing.
resource "google_service_account" "cutoff" {
  count        = local.cutoff_enabled ? 1 : 0
  account_id   = "agent-commons-cutoff"
  display_name = "Agent Commons billing cutoff (unlinks billing at the cutoff)"
}

resource "google_project_iam_member" "cutoff_unlinks_billing" {
  count   = local.cutoff_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/billing.projectManager"
  member  = "serviceAccount:${google_service_account.cutoff[0].email}"
}

# Builds the function's image. The project has no Compute Engine default account to build with.
resource "google_service_account" "cutoff_build" {
  count        = local.cutoff_enabled ? 1 : 0
  account_id   = "agent-commons-cutoff-build"
  display_name = "Agent Commons billing cutoff (builds the function)"
}

resource "google_project_iam_member" "cutoff_build" {
  count   = local.cutoff_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${google_service_account.cutoff_build[0].email}"
}

resource "google_storage_bucket" "functions" {
  count                       = local.cutoff_enabled ? 1 : 0
  name                        = "${var.project_id}-functions"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  depends_on                  = [google_project_service.operations]
}

data "archive_file" "billing_cutoff" {
  type        = "zip"
  source_dir  = "${path.module}/billing_cutoff"
  output_path = "${path.module}/.terraform/billing_cutoff.zip"
  excludes    = ["__pycache__"]
}

resource "google_storage_bucket_object" "billing_cutoff" {
  count  = local.cutoff_enabled ? 1 : 0
  name   = "billing_cutoff-${data.archive_file.billing_cutoff.output_md5}.zip"
  bucket = google_storage_bucket.functions[0].name
  source = data.archive_file.billing_cutoff.output_path
}

resource "google_cloudfunctions2_function" "billing_cutoff" {
  count    = local.cutoff_enabled ? 1 : 0
  name     = "agent-commons-billing-cutoff"
  location = var.region

  build_config {
    runtime         = "python312"
    entry_point     = "stop_billing"
    service_account = google_service_account.cutoff_build[0].id
    source {
      storage_source {
        bucket = google_storage_bucket.functions[0].name
        object = google_storage_bucket_object.billing_cutoff[0].name
      }
    }
  }
  service_config {
    max_instance_count    = 1
    available_memory      = "256M"
    timeout_seconds       = 60
    ingress_settings      = "ALLOW_INTERNAL_ONLY"
    service_account_email = google_service_account.cutoff[0].email
    environment_variables = {
      PROJECT_ID = var.project_id
    }
  }
  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.billing_cutoff[0].id
    retry_policy          = "RETRY_POLICY_RETRY"
    service_account_email = google_service_account.cutoff[0].email
  }
  depends_on = [google_project_service.cutoff, google_project_iam_member.cutoff_build]
}

# The trigger delivers messages as the function's own account.
resource "google_cloud_run_v2_service_iam_member" "cutoff_invokes" {
  count    = local.cutoff_enabled ? 1 : 0
  name     = google_cloudfunctions2_function.billing_cutoff[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cutoff[0].email}"
}
