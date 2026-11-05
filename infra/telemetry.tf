# Telemetry (decision 0037): when var.otlp_endpoint is set, each server exports traces, metrics
# and logs there over OTLP. The endpoint's headers (the credentials) go through Secret Manager
# like the database URL; the endpoint itself and the environment's name are plain variables.
resource "google_secret_manager_secret" "otlp_headers" {
  for_each  = var.otlp_endpoint == "" ? {} : local.environments
  secret_id = "agent-commons-${each.key}-otlp-headers"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "otlp_headers" {
  for_each    = google_secret_manager_secret.otlp_headers
  secret      = each.value.id
  secret_data = var.otlp_headers
}

resource "google_secret_manager_secret_iam_member" "server_reads_otlp_headers" {
  for_each  = google_secret_manager_secret.otlp_headers
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.server[each.key].email}"
}

locals {
  # The container's telemetry environment: always the environment's name (it labels every
  # signal), the endpoint and its headers only when exporting.
  telemetry_env = {
    for env in keys(local.environments) : env => merge(
      { OTEL_RESOURCE_ATTRIBUTES = "deployment.environment.name=${env}" },
      var.otlp_endpoint == "" ? {} : { OTEL_EXPORTER_OTLP_ENDPOINT = var.otlp_endpoint },
    )
  }
}
