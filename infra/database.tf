# Neon free plan: Postgres 16 with pgvector, scales to zero when idle (decision 0020).
resource "neon_project" "commons" {
  name       = "agent-commons"
  region_id  = var.neon_region
  pg_version = 16

  # The free plan keeps at most 6 hours of restore history.
  history_retention_seconds = 21600

  branch {
    name          = "main"
    database_name = "commons"
    role_name     = "commons"
  }

  primary_compute {
    autoscaling_limit_min_cu = 0.25
    autoscaling_limit_max_cu = 1
  }
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "agent-commons-database-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Direct (unpooled) connection: psycopg uses server-side prepared statements.
resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = neon_project.commons.connection_uri
}
