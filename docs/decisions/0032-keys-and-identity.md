# 0032. Keys: several per reporter, scoped, rotatable, revocable
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: Each reporter has exactly one key (a hash on `reporters`), created by `commons admin
create-key`. Rotation means re-inviting, and there is no revocation, expiry or record of use.
The owner's agents and the owner's dashboard login share one admin key. That key was printed in
a session on 2026-10-31 and is still live. Standard API-key practice:
- Store only hashes (done).
- Give each key a visible prefix so it can be identified without revealing it.
- Allow several keys per identity, so rotation can overlap with no downtime.
- Scope each key to least privilege: an agent key should not be an admin key.
- Record last use, support expiry and revocation, and log key administration.

Decision:
1. An `api_keys` table with id, reporter, the first 8 characters (shown in listings), hash,
   scopes, label, `created_at`, `last_used_at`, `expires_at`, `revoked_at`, and a `test` flag
   (0030). The current `reporters.api_key_hash` values migrate to one key each, and the column
   is dropped.
2. Scopes:
   - `agent`: search, propose, report, status and the hook API.
   - `review`: dashboard reviews (0028).
   - `admin`: everything, including key management.
   Agent sessions get `agent` keys only. The dashboard takes a separately issued `admin` or
   `review` key. `commons join` refuses an admin key with a pointer to create an agent key.
3. Commands, mirrored on the dashboard's People page:
   - `commons admin key create <pseudonym> --scope agent [--label laptop] [--expires 90d]`
   - `commons admin key list [<pseudonym>]`
   - `commons admin key rotate <key-prefix>`: creates the replacement, prints the invite
     snippet, and revokes the old key after a grace period (default 24 hours) or at once with
     `--now`.
   - `commons admin key revoke <key-prefix>`
   `commons admin invite` keeps working on top of these.
4. Updating `last_used_at` is throttled to once a minute per key. Requests with a revoked or
   expired key get 401 with "key revoked" or "key expired". Key administration goes into the
   call log (decision 0024).
5. Immediately, before the code exists: rotate the owner's printed key with today's commands
   (`commons admin create-key zach --admin`), update the owner's MCP registration and
   dashboard login, and confirm the old key gets 401.

Consequences: a leaked key can be killed in one command without re-inviting anyone, and an
agent key can no longer approve entries or open the dashboard. The migration touches auth for
every request, so it needs careful tests and a staging soak before production.
