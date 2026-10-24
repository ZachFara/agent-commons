# Hosting Agent Commons on Neon + Cloud Run

Terraform for the hosted environments (decisions 0020, 0022). Postgres on **Neon**, the server
(`commons-server`) on **Google Cloud Run**, images in **Artifact Registry**, database URLs in
**Secret Manager**, deploys from **GitHub Actions** with keyless auth. Starts at $0/month; see
[Costs](#costs).

| Environment | Cloud Run service | Neon branch | Deployed |
|---|---|---|---|
| dev | your machine (`make db`, `commons-dashboard`) | local Docker | — |
| staging | `agent-commons-staging` | `staging` | automatically on every merge to `main` |
| production | `agent-commons` | `production` | the same image, after approval in GitHub |

To move providers later, keep the image (`server/Dockerfile`) and rewrite this directory: the app
only needs a Postgres URL in `COMMONS_DATABASE_URL` and a port in `$PORT`.

## What you need

- Google Cloud project with billing (here `agent-commons-512623`) and `gcloud auth login`.
- The Neon project (here `weathered-cloud-96612464`, created in the console; Terraform imports
  it) and a Neon API key saved to `~/.config/agent-commons/neon_api_key` (mode 600).
- Terraform 1.9+ and `gh` logged in to this repository.

## One-time setup

```sh
export PROJECT=agent-commons-512623
gcloud config set project $PROJECT

# State bucket (free under 5 GB in us-central1)
gcloud storage buckets create gs://$PROJECT-tfstate --location us-central1 --uniform-bucket-level-access
gcloud storage buckets update gs://$PROJECT-tfstate --versioning

cd infra
cp terraform.tfvars.example terraform.tfvars          # project_id, neon_project_id
export NEON_API_KEY="$(cat ~/.config/agent-commons/neon_api_key)"
export GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"   # valid ~1 hour
terraform init -backend-config="bucket=$PROJECT-tfstate"
terraform plan                                       # read it: one import, nothing destroyed
terraform apply

# GitHub: repository variables, and approval before production
terraform output -json github_variables | python3 -c \
  'import json,sys,subprocess; [subprocess.run(["gh","variable","set",k,"--body",v],check=True) for k,v in json.load(sys.stdin).items()]'
gh api -X PUT repos/ZachFara/agent-commons/environments/staging
gh api -X PUT repos/ZachFara/agent-commons/environments/production \
  -F "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user -q .id)"

gh workflow run deploy && gh run watch                # staging deploys, production waits for you
terraform output urls
```

After this, every merge to `main` deploys to staging; approve the waiting `production` job in
the Actions tab to promote the same image.

## Admin keys and invites

Cloud Run has no shell, so run admin commands from your machine against an environment's
database:

```sh
export COMMONS_DATABASE_URL="$(gcloud secrets versions access latest --secret agent-commons-production-database-url)"
uv run commons admin create-key <your-pseudonym> --admin
uv run commons admin invite <friend> --server "$(cd infra && terraform output -json urls | python3 -c 'import json,sys; print(json.load(sys.stdin)["production"])')"
```

Use `agent-commons-staging-database-url` and the staging URL to do the same on staging. Open
`<url>/` and sign in with any user name and an admin key as the password.

## Costs

Expected bill at the start: **$0/month**. Free-tier figures as of October 2026; check the
providers' pricing pages before relying on them.

| Service | Free tier | Agent Commons at a few users | When it would cost |
|---|---|---|---|
| Neon Postgres | 0.5-1 GB storage, 100 compute-hours/month per project, scales to zero after 5 min idle | well under 1 GB; two branches, each computing only while in use | Past the free limits Neon suspends compute until next month rather than billing; the usage-based Launch plan is about $0.11 per compute-hour and $0.35/GB-month |
| Cloud Run | 2M requests, 180k vCPU-seconds, 360k GiB-seconds per month (us-central1), shared by both services | ~5 people × 30 sessions/day × ~100 hook calls ≈ 450k requests and ~45k vCPU-s; staging adds little | Beyond that, about $0.40 per million requests and $0.000024 per vCPU-second |
| Artifact Registry | 0.5 GB storage | 3 images × ~150 MB | $0.10/GB-month over 0.5 GB |
| Secret Manager | 6 secret versions, 10k accesses/month | 2 secrets, read at instance start | $0.06 per version, $0.03 per 10k accesses |
| Cloud Storage (state) | 5 GB in us-central1 | kilobytes | negligible |
| GitHub Actions | free for public repositories | a few minutes per deploy | — |

Budgets for this project were set up in the console (a $5 alert to both email addresses and a $4
Cloud Run spend cap that pauses Cloud Run), so leave `billing_account` unset. Production scales to
at most 2 instances and staging to 1.

Cold starts: after a quiet period the first request wakes Cloud Run (~1-3 s) and Neon (~0.5 s).
Hooks allow 30-60 s, so this only shows up as a slightly slower first hook.

## Tear down

`terraform destroy` removes the Google Cloud resources and the staging branch. The Neon project
is protected (`prevent_destroy`) because it holds production data; delete it in the Neon console
if you really mean to.
