terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    neon = {
      source  = "kislerdm/neon" # community-maintained; Neon documents it but doesn't support it
      version = "~> 0.18"
    }
  }

  # State lives in a GCS bucket you create once (see README). Pass it at init:
  #   terraform init -backend-config="bucket=<project>-tfstate"
  backend "gcs" {
    prefix = "agent-commons"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# The budgets API needs a quota project when called with user credentials.
provider "google" {
  alias                 = "billing"
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

# Reads NEON_API_KEY from the environment.
provider "neon" {}
