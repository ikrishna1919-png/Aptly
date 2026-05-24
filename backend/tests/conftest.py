import os

# Use an in-memory SQLite DB for tests so CI doesn't require Postgres.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("ENVIRONMENT", "test")
