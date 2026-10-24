locals {
  apis = [
    "artifactregistry.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sts.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "agent-commons"
  location      = var.region
  format        = "DOCKER"
  description   = "Agent Commons server images (shared by staging and production)"

  # Stay inside the 0.5 GB free storage: images are ~150 MB, so keep the 3 newest.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "604800s"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_service_account" "server" {
  for_each     = local.environments
  account_id   = "agent-commons-${each.key}"
  display_name = "Agent Commons server (${each.key})"
}

resource "google_secret_manager_secret_iam_member" "server_reads_database_url" {
  for_each  = local.environments
  secret_id = google_secret_manager_secret.database_url[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.server[each.key].email}"
}

resource "google_cloud_run_v2_service" "server" {
  for_each            = local.environments
  name                = each.value.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.server[each.key].email
    max_instance_request_concurrency = 40

    scaling {
      min_instance_count = 0 # scale to zero: free when idle
      max_instance_count = each.value.max_instances
    }

    containers {
      image = var.image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true # request-based billing, the free-tier mode
        startup_cpu_boost = true
      }
      env {
        name = "COMMONS_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url[each.key].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  # GitHub Actions deploys new images; Terraform owns everything else. The service-level
  # `scaling` block is filled in by Cloud Run (limits live in template.scaling above).
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version, scaling]
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_iam_member.server_reads_database_url,
  ]
}

# Public endpoints: the app authenticates every request itself (API keys, admin Basic auth).
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = local.environments
  name     = google_cloud_run_v2_service.server[each.key].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
