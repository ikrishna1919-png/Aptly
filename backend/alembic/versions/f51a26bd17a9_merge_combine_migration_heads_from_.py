"""merge: combine migration heads from parallel PRs

Revision ID: f51a26bd17a9
Revises: 0018_seed_more_sources, 0018_tailor_runs
Create Date: 2026-05-29 00:28:01.340284

"""

from collections.abc import Sequence

revision: str = "f51a26bd17a9"
down_revision: str | None = ("0018_seed_more_sources", "0018_tailor_runs")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
