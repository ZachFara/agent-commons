# 0033. Operations: backups with restore drills, alerting, budget, retention
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: Production holds the project's dataset, but its only safety net is Neon's history
window. The free plan keeps 6 hours (`infra/database.tf`), so anything noticed later than that
is unrecoverable. Nothing alerts on errors or downtime. The console-made $4 Cloud Run spend cap
pauses Cloud Run (`infra/README.md`), which would take production down with nobody told.
Retention for `searches` and call logs is an open question (decision 0024). The standard
baseline for a small production service:
- Backups with stated recovery targets (RPO and RTO), and restore drills, since an untested
  backup does not count.
- An external uptime check and alerts on error rate.
- Budget alerts that warn before anything stops.
- Written retention and a runbook.

Decision:
1. Backups, targeting RPO 24 hours and RTO 1 hour:
   - A nightly GitHub Actions workflow runs `pg_dump` in custom format against production,
     using a read-only role (the `commons_viewer` pattern).
   - The dump is encrypted with `age` to a public key whose private half the owner keeps
     offline, then uploaded to a Google Cloud Storage bucket created by Terraform.
   - Bucket lifecycle: keep 14 daily and 6 monthly copies. It fits the free 5 GB.
   - Neon's 6-hour history stays the first resort for very recent mistakes.
2. Restore drill:
   - Monthly, the same workflow restores the newest dump into a scratch Postgres (a service
     container), then runs `commons migrate` and compares row counts with production.
   - It fails loudly if anything is off.
   - A written runbook in `infra/README.md` covers a real restore: into a new Neon branch,
     then repoint the secret.
3. Alerting, using free Cloud Monitoring features, by email to the owner:
   - An uptime check on `/health` every 5 minutes, alerting after 2 failures.
   - A log-based metric on call-log lines with severity ERROR, plus HTTP 5xx, alerting on 5
     or more in 10 minutes.
   - All defined in Terraform.
4. Budget:
   - Bring the console budgets into Terraform.
   - Email at 50% and 90% of the monthly amount, before the cap.
   - Keep the cap, because a runaway bill is worse than an outage, but its pause is now
     visible through the uptime alert. The runbook says how to unpause.
5. Retention:
   - `searches` rows older than 180 days are folded into a monthly table of query, misses
     and count, then deleted.
   - Call logs keep Cloud Logging's default 30 days.
   - Spools keep their 30 days.
   - Excluded rows (0030) follow the same rules.
   - A nightly job (with `commons rank`) applies this.

Consequences: data loss is bounded to a day and found by drill, not by disaster, and outages
are noticed within minutes. Costs: a bucket, an encryption key the owner must keep safe, and a
few Terraform resources, all within free tiers at current volume. The restore drill uses
GitHub Actions minutes, which are free for this public repository.

Implementation notes (T43, 2026-10-31):
- The drill can't decrypt with a key that stays offline, so each backup is encrypted to two
  age recipients: the owner's recovery key (offline) and a drill key held only as a GitHub
  secret. Either decrypts; the drill key can be rotated without touching old backups' recovery.
- Retention runs as `commons retain` at the end of the nightly backup workflow rather than
  inside `commons rank`, so it runs once a night against production instead of on every
  machine whose SessionStart re-ranks.
- Each night's dump is also restored into a scratch Postgres before upload, so a broken dump
  fails that night rather than at the monthly drill.
