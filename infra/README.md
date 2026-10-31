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

## Backups, alerts and retention (decision 0033)

Targets: lose at most a day of data (RPO 24 h), and be back within an hour (RTO 1 h).

- **Backups**: `.github/workflows/backup.yml` runs nightly. It dumps production through the
  read-only `commons_backup` role, restores the dump into a scratch Postgres to prove it works,
  encrypts it with `age`, and uploads it to `gs://agent-commons-512623-backups/daily/<time>/`
  (also `monthly/` on the 1st). The bucket keeps 14 daily and 6 monthly copies.
- **Restore drill**: on the 2nd of each month the same workflow decrypts the newest backup,
  restores it, and checks every table's row count against the counts saved at backup time.
  Run it any time with `gh workflow run backup -f drill=true`.
- **Keys**: backups are encrypted to two age keys. The recovery key
  (`~/.config/agent-commons/backup-recovery.agekey`) belongs in your password manager and nowhere
  else. The drill key lives only in the GitHub secret `BACKUP_DRILL_IDENTITY`; rotate it by
  re-running `setup-backup-keys.sh`. Re-key the role with `uv run python tools/backup_role.py`.
- **Alerts**, by email to `alert_emails` in `terraform.tfvars`: production `/health` failing
  from more than one region for 10 minutes, 5 or more server errors in 10 minutes, and spend at
  50% and 90% of `budget_alert_usd` (the console's $4 Cloud Run cap).
- **Retention**: after each backup, `commons retain` folds searches older than 180 days into
  `search_months` (query, searches, misses) and deletes them. Cloud Logging keeps call logs for
  its default 30 days; hook spools on each machine keep 30 days.

### When an alert fires

- **Production is down**: open the Cloud Run service in the console. If billing paused it (the
  spend cap), the budget emails will have come first: raise the cap or wait for the month to
  turn, then redeploy (`gh workflow run deploy`). Otherwise read the newest revision's logs, and
  roll back with `gcloud run services update-traffic agent-commons --to-revisions <previous>=100
  --region us-central1`.
- **Server errors**: `gcloud logging read 'resource.labels.service_name="agent-commons" AND
  severity>=ERROR' --limit 20 --freshness 1h`. Call-log lines name the tool that failed.
- **A backup or drill failed**: GitHub emails the failed run. Re-run it from the Actions tab
  once fixed; a missed night only widens that day's RPO.

### Restoring production

1. Mistake in the last 6 hours: restore in the Neon console (Branches, Restore) to a time
   before it. Nothing else is needed.
2. Older: download a backup and decrypt it with the recovery key:
   `gcloud storage cp gs://agent-commons-512623-backups/daily/<time>/commons.dump.age . &&
   age -d -i ~/.config/agent-commons/backup-recovery.agekey -o commons.dump commons.dump.age`
3. Create a new Neon branch from production (Neon console), and restore into it:
   `pg_restore --no-owner --no-privileges --clean --if-exists -d "<new branch URL>" commons.dump`
   (Postgres 18 client tools).
4. Check it with `uv run python tools/backup_counts.py compare "<new branch URL>" counts.json`.
5. Point production at it: `gcloud secrets versions add agent-commons-production-database-url
   --data-file=-` with the new URL, then redeploy so Cloud Run reads the new version.

## Costs

Expected bill at the start: **$0/month**. Free-tier figures as of October 2026; check the
providers' pricing pages before relying on them.

| Service | Free tier | Agent Commons at a few users | When it would cost |
|---|---|---|---|
| Neon Postgres | 0.5-1 GB storage, 100 compute-hours/month per project, scales to zero after 5 min idle | well under 1 GB; two branches, each computing only while in use | Past the free limits Neon suspends compute until next month rather than billing; the usage-based Launch plan is about $0.11 per compute-hour and $0.35/GB-month |
| Cloud Run | 2M requests, 180k vCPU-seconds, 360k GiB-seconds per month (us-central1), shared by both services | ~5 people × 30 sessions/day × ~100 hook calls ≈ 450k requests and ~45k vCPU-s; staging adds little | Beyond that, about $0.40 per million requests and $0.000024 per vCPU-second |
| Artifact Registry | 0.5 GB storage | 3 images × ~150 MB | $0.10/GB-month over 0.5 GB |
| Secret Manager | 6 secret versions, 10k accesses/month | 3 secrets (two database URLs, the backup role's URL) | $0.06 per version, $0.03 per 10k accesses |
| Cloud Storage (state, backups) | 5 GB in us-central1 | kilobytes of state; ~20 encrypted dumps of ~50 KB each today | $0.02/GB-month over 5 GB |
| Cloud Monitoring | uptime checks and alerting free at this scale | one uptime check, two alert policies, one log metric | — |
| GitHub Actions | free for public repositories | a few minutes per deploy | — |

The $4 Cloud Run spend cap that pauses Cloud Run was set up in the console and stays there.
Terraform owns the budget alerts (50% and 90% of that amount, `billing_account` in
`terraform.tfvars`); the console also has an older $5 alert. Production scales to at most 2
instances and staging to 1. Backups, monitoring and alerting fit their free tiers.

Cold starts: after a quiet period the first request wakes Cloud Run (~1-3 s) and Neon (~0.5 s).
Hooks allow 30-60 s, so this only shows up as a slightly slower first hook.

## Tear down

`terraform destroy` removes the Google Cloud resources and the staging branch. The Neon project
is protected (`prevent_destroy`) because it holds production data; delete it in the Neon console
if you really mean to.
