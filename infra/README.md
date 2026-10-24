# Hosting Agent Commons on Neon + Cloud Run

Terraform for a group server (decision 0020): Postgres on **Neon**, the server
(`commons-server`) on **Google Cloud Run**, images in **Artifact Registry**, the database URL in
**Secret Manager**, and deploys from **GitHub Actions** with keyless auth. Everything starts at
$0/month; see [Costs](#costs).

To move to another provider later, keep the image (`server/Dockerfile`) and rewrite this
directory: the app only needs a Postgres URL in `COMMONS_DATABASE_URL` and a port in `$PORT`.

## What you need

- A Google Cloud account with billing enabled (Cloud Run requires a billing account even when
  usage stays in the free tier) and the `gcloud` CLI.
- A Neon account and an API key (Neon console → Account settings → API keys).
- Terraform 1.9+, or Docker (`make infra-check` uses the official image).
- `gh` CLI logged in to this repository.

## One-time setup

```sh
# 1. Project and billing
export PROJECT=agent-commons-$RANDOM
gcloud projects create $PROJECT
gcloud billing accounts list                         # pick the ACCOUNT_ID
gcloud billing projects link $PROJECT --billing-account <ACCOUNT_ID>
gcloud config set project $PROJECT
gcloud auth application-default login

# 2. A bucket for Terraform state (free under 5 GB in us-central1)
gcloud storage buckets create gs://$PROJECT-tfstate --location us-central1 --uniform-bucket-level-access
gcloud storage buckets update gs://$PROJECT-tfstate --versioning

# 3. Apply
export NEON_API_KEY=<your Neon API key>
cd infra
cp terraform.tfvars.example terraform.tfvars          # set project_id (and billing_account for a $5 alert)
terraform init -backend-config="bucket=$PROJECT-tfstate"
terraform apply                                      # if an API "is not enabled yet", wait a minute and re-run

# 4. Let GitHub Actions deploy
terraform output -json github_variables | python3 -c \
  'import json,sys,subprocess; [subprocess.run(["gh","variable","set",k,"--body",v],check=True) for k,v in json.load(sys.stdin).items()]'
gh workflow run deploy                               # builds the image and deploys it
gh run watch
terraform output server_url                          # https://agent-commons-....run.app
```

After this, every merge to `main` redeploys automatically.

## Admin keys

Cloud Run has no shell, so mint keys from your machine against the Neon database:

```sh
export COMMONS_DATABASE_URL="$(gcloud secrets versions access latest --secret agent-commons-database-url)"
uv run commons admin create-key <your-pseudonym> --admin
```

Open `<server_url>/` and sign in with any user name and that key as the password.

## Costs

Expected bill at the start: **$0/month**. Free-tier figures as of October 2026; check the
providers' pricing pages before relying on them.

| Service | Free tier | Agent Commons at a few users | When it would cost |
|---|---|---|---|
| Neon Postgres | 0.5-1 GB storage, 100 compute-hours/month, scales to zero after 5 min idle | well under 1 GB; compute only while sessions are active | Past the free limits Neon suspends compute until next month rather than billing; the usage-based Launch plan is about $0.11 per compute-hour and $0.35/GB-month |
| Cloud Run | 2M requests, 180k vCPU-seconds, 360k GiB-seconds per month (us-central1) | ~5 people × 30 sessions/day × ~100 hook calls ≈ 450k requests and ~45k vCPU-s | Beyond that, about $0.40 per million requests and $0.000024 per vCPU-second |
| Artifact Registry | 0.5 GB storage | 3 images × ~150 MB | $0.10/GB-month over 0.5 GB |
| Secret Manager | 6 secret versions, 10k accesses/month | 1 secret, read at instance start | $0.06 per version, $0.03 per 10k accesses |
| Cloud Storage (state) | 5 GB in us-central1 | kilobytes | negligible |
| GitHub Actions | free for public repositories | a few minutes per deploy | — |

Set `billing_account` in `terraform.tfvars` to get an email if a month passes $5 (it alerts; it
never shuts anything down). `max_instances` (default 2) caps how far Cloud Run can scale.

Cold starts: after a quiet period the first request wakes Cloud Run (~1-3 s) and Neon (~0.5 s).
Hooks allow 30-60 s, so this only shows up as a slightly slower first hook.

## Tear down

`terraform destroy` removes everything except the state bucket and the project.
