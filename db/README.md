# db

Postgres 16 with pgvector for local mode.

```
cd db && docker compose up -d --wait
```

Defaults (override with environment variables or a gitignored `db/.env`):

| Variable | Default |
|---|---|
| `COMMONS_DB_USER` | `commons` |
| `COMMONS_DB_PASSWORD` | `commons` |
| `COMMONS_DB_NAME` | `commons` |
| `COMMONS_DB_PORT` | `5432` (bound to 127.0.0.1 only) |

The server connects with `COMMONS_DATABASE_URL`, default
`postgresql://commons:commons@localhost:5432/commons`.

Schema is created by `commons migrate`, not by init scripts, so local and hosted databases are
built the same way. Data lives in the `agent-commons_pgdata` volume;
`docker compose down -v` deletes it.
