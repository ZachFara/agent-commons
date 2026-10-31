# Spend alerts (decision 0033): email at 50% and 90% of var.budget_alert_usd, set to the console's
# Cloud Run spend cap, so the owner hears before the cap pauses Cloud Run. The cap itself stays
# in the console. Needs var.billing_account; skipped when it is empty.
resource "google_project_service" "billing_budgets" {
  count              = var.billing_account == "" ? 0 : 1
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

resource "google_billing_budget" "monthly" {
  provider        = google.billing
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
    threshold_percent = 0.9
  }
  all_updates_rule {
    monitoring_notification_channels = local.alert_channels
  }
  depends_on = [google_project_service.billing_budgets]
}
