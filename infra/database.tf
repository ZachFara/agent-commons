# Neon: one project, one branch per environment (decision 0022).
# The project was created in the Neon console, so Terraform imports it rather than creating it.
import {
  to = neon_project.commons
  id = var.neon_project_id
}

resource "neon_project" "commons" {
  name      = "Commons"
  region_id = var.neon_region

  # It holds production data: Terraform must never delete it.
  lifecycle {
    prevent_destroy = true
  }
}

# Staging is a branch of production with its own compute. Free-plan computes scale to zero.
resource "neon_branch" "staging" {
  project_id = neon_project.commons.id
  parent_id  = neon_project.commons.default_branch_id
  name       = "staging"
}

resource "neon_endpoint" "staging" {
  project_id = neon_project.commons.id
  branch_id  = neon_branch.staging.id
  type       = "read_write"
}

locals {
  neon_branch_ids = {
    production = neon_project.commons.default_branch_id
    staging    = neon_branch.staging.id
  }
  neon_hosts = {
    production = neon_project.commons.database_host
    staging    = neon_endpoint.staging.host
  }
}

# A `commons` role and database per branch, so Terraform knows each password and the
# environments never share credentials. The staging branch is created first, so it doesn't
# inherit the production role.
resource "neon_role" "commons" {
  for_each   = local.environments
  project_id = neon_project.commons.id
  branch_id  = local.neon_branch_ids[each.key]
  name       = "commons"
  depends_on = [neon_branch.staging]
}

resource "neon_database" "commons" {
  for_each   = local.environments
  project_id = neon_project.commons.id
  branch_id  = local.neon_branch_ids[each.key]
  name       = "commons"
  owner_name = neon_role.commons[each.key].name
}

resource "google_secret_manager_secret" "database_url" {
  for_each  = local.environments
  secret_id = "agent-commons-${each.key}-database-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Direct (unpooled) connection: psycopg uses server-side prepared statements.
resource "google_secret_manager_secret_version" "database_url" {
  for_each    = local.environments
  secret      = google_secret_manager_secret.database_url[each.key].id
  secret_data = "postgresql://${neon_role.commons[each.key].name}:${neon_role.commons[each.key].password}@${local.neon_hosts[each.key]}/${neon_database.commons[each.key].name}?sslmode=require"
}
