# 0022. Staging and production environments
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: The owner wants a stable instance a friend can use and one to test on. The Neon project
was created in the console (branch `production`, database `neondb`); Google Cloud already has a
project (`agent-commons-512623`) with a $5 budget alert and a $4 Cloud Run spend cap.

Decision:
- Three environments: dev (local Docker), staging, production.
- Neon: the console-created project is imported (`import` block) and protected with
  `prevent_destroy`. Staging is a Neon branch of production with its own compute. Each branch
  gets a Terraform-created `commons` role and database; staging is branched before the
  production role exists, so the two never share a password. Compute settings are left at
  free-plan defaults (the free plan can't change them).
- Google Cloud: one project; per environment a Cloud Run service (`agent-commons`,
  `agent-commons-staging`), service account, and database-URL secret; one image registry and
  one deployer identity.
- Delivery: every merge to `main` builds one image, deploys it to staging, and smoke-tests
  `/healthz`; the same image is promoted to production after approval on the GitHub
  `production` environment.
- Terraform authenticates to Google with `gcloud auth print-access-token`, and to Neon with an
  API key kept in a 0600 file outside the repo.

Consequences: staging catches problems on real infrastructure before the friend sees them, with
no extra cost (both fit the free tiers). The spend cap covers Cloud Run for both services.
