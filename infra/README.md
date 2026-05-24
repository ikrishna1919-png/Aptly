# infra

Local development infrastructure. Currently just Postgres.

## Start Postgres

```bash
docker compose -f infra/docker-compose.yml up -d
```

Connection string:

```
postgresql+psycopg://aptly:aptly@localhost:5432/aptly
```

Data is persisted in a Docker volume named `pgdata`. To wipe:

```bash
docker compose -f infra/docker-compose.yml down -v
```
