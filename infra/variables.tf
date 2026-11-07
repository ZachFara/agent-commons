variable "project_id" {
  description = "Google Cloud project to deploy into."
  type        = string
}

variable "region" {
  description = "Cloud Run and Artifact Registry region. us-central1 is in the free tier."
  type        = string
  default     = "us-central1"
}

variable "neon_project_id" {
  description = "ID of the existing Neon project to import (Neon console, Settings, General)."
  type        = string
}

variable "neon_region" {
  description = "Region of the Neon project; must match the existing project."
  type        = string
  default     = "aws-us-east-2"
}

variable "github_repository" {
  description = "owner/name of the repository allowed to deploy via GitHub Actions."
  type        = string
  default     = "ZachFara/agent-commons"
}

variable "image" {
  description = "Initial container image. Deploys after the first one come from GitHub Actions."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "billing_account" {
  description = "Billing account ID for a Terraform-managed spend alert (optional; empty to skip)."
  type        = string
  default     = ""
}

variable "budget_alert_usd" {
  description = "Monthly budget; alerts at 50% and 90%. Matches the console's Cloud Run cap."
  type        = number
  default     = 4
}

variable "cutoff_usd" {
  description = "Monthly spend at which billing is unlinked from the project, stopping every service in it (decision 0038)."
  type        = number
  default     = 100
}

variable "alert_emails" {
  description = "Addresses for uptime, error and budget alerts (set in terraform.tfvars)."
  type        = list(string)
  default     = []
}

variable "otlp_endpoint" {
  description = "OTLP/HTTP endpoint the servers send traces, metrics and logs to (decision 0037), e.g. Grafana Cloud's https://otlp-gateway-<region>.grafana.net/otlp. Empty: no telemetry."
  type        = string
  default     = ""
}

variable "otlp_headers" {
  description = "Headers for that endpoint, as OTEL_EXPORTER_OTLP_HEADERS expects them, e.g. \"Authorization=Basic <base64 of instance:token>\". Kept in terraform.tfvars and Secret Manager."
  type        = string
  default     = ""
  sensitive   = true
}
