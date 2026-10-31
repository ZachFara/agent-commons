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

variable "alert_emails" {
  description = "Addresses for uptime, error and budget alerts (set in terraform.tfvars)."
  type        = list(string)
  default     = []
}
