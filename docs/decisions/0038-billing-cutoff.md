# 0038. Billing cutoff at $100
Status: Accepted (owner, 2026-11-07)
Date: 2026-11-07

Context: The only spend cap is the console's $4 cap on Cloud Run (decision 0033). Artifact
Registry, Secret Manager, Cloud Storage, Logging and Monitoring are covered by email alerts only,
so a runaway bill in any of them would keep growing until someone acted. The owner asked for a
hard stop: at $100 a month, shut everything down and notify them.

Decision:
- A Terraform-managed budget, "Agent Commons billing cutoff", covers the whole project at
  `cutoff_usd` ($100). Cloud Billing emails `alert_emails` at 50%, 90% and 100%.
- The budget also publishes to a Pub/Sub topic. A Cloud Function (`infra/billing_cutoff/`)
  reads each notification and, once `costAmount` reaches `budgetAmount`, unlinks billing from
  the project. This is Google's documented way to cap spend.
- The function's account holds only Project Billing Manager on this project. Google's sample
  grants Billing Account Administrator, which would reach every project on the account.
- The $4 Cloud Run cap and the $4 alert budget stay as they are.

Consequences: Spend this month is about $0.10, so the cutoff should never fire in normal use.
If it does, every Google Cloud service in the project stops, and Google may delete resources,
including the backup and state buckets. Neon, which holds the data, is outside Google Cloud and
is not affected. Billing data lags by hours, so spend can pass $100 before the cutoff acts.
Re-linking is manual (`infra/README.md`). Revisit if the backups need a copy outside Google
Cloud.
