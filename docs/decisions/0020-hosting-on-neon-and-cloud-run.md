# 0020. Host on Neon and Cloud Run, with Terraform
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: The owner wants generous free tiers and infrastructure as code, so providers can be
swapped. Options checked in October 2026: Neon (free Postgres, 0.5-1 GB, 100 CU-hours, scales to
zero; Terraform provider is community-maintained), Supabase (500 MB, but free projects pause
after 7 days without traffic), Google Cloud Run (2M requests and 180k vCPU-seconds free, HTTPS,
scale to zero, official Terraform provider), Fly.io (no free tier for new accounts; dropped its
Terraform provider), Render (free services sleep after 15 minutes with 30-60 s cold starts).

Decision: Neon for Postgres and Cloud Run for `commons-server` (decision 0019), with Artifact
Registry for images, Secret Manager for the database URL, a GCS bucket for Terraform state, and
GitHub Actions deploying through Workload Identity Federation (no stored keys; only `main` of
this repository may deploy). Terraform lives in a new top-level `infra/` directory; it is
validated in CI by a separate `infra` job (Terraform isn't a Python dev dependency) and locally
with `make infra-check`. The app uses Neon's direct connection, not the pooler, because psycopg
uses server-side prepared statements.

Consequences: $0/month at the start. Switching providers means rewriting `infra/` only; the image
needs just `COMMONS_DATABASE_URL` and `$PORT`. The Neon provider is community-maintained: pin it
and read its changelog before upgrading.
