# Optional spend alert: emails the billing admins if the month passes var.budget_alert_usd.
# It only alerts; it never shuts anything down.
resource "google_project_service" "billing_budgets" {
  count              = var.billing_account == "" ? 0 : 1
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

resource "google_billing_budget" "monthly" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "Agent Commons monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_alert_usd)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  depends_on = [google_project_service.billing_budgets]
}
