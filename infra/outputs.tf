output "urls" {
  description = "Per environment: members connect to <url>/mcp; admins open <url>/."
  value       = { for env, service in google_cloud_run_v2_service.server : env => service.uri }
}

output "image_repository" {
  description = "Where GitHub Actions pushes images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/server"
}

output "github_variables" {
  description = "Set these as GitHub repository variables (see README)."
  value = {
    GCP_PROJECT_ID   = var.project_id
    GCP_REGION       = var.region
    GCP_WIF_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_DEPLOYER     = google_service_account.deployer.email
    GCP_OPERATIONS   = google_service_account.operations.email
    BACKUP_BUCKET    = google_storage_bucket.backups.name
  }
}
