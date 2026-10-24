variable "project_id" {
  description = "Google Cloud project to deploy into."
  type        = string
}

variable "region" {
  description = "Cloud Run and Artifact Registry region. us-central1 is in the free tier."
  type        = string
  default     = "us-central1"
}

variable "neon_region" {
  description = "Neon region; aws-us-east-2 (Ohio) is close to us-central1."
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

variable "max_instances" {
  description = "Upper bound on Cloud Run instances; keeps a runaway from leaving the free tier."
  type        = number
  default     = 2
}

variable "billing_account" {
  description = "Billing account ID for a spend alert (optional; empty to skip the budget)."
  type        = string
  default     = ""
}

variable "budget_alert_usd" {
  description = "Monthly amount that triggers budget alert emails."
  type        = number
  default     = 5
}
