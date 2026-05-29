"""create tailor_runs table

Revision ID: 0018_tailor_runs
Revises: 0017_profile_saved_at
Create Date: 2026-05-28

The resume-tailoring flow moves from synchronous request/response to the
same background-job pattern `parse_runs` uses: `POST /api/tailor/start`
returns immediately with a `run_id`, a worker runs the analyze step
(Anthropic), the user answers the gap questions, and a second worker
streams the generated resume. Status + every intermediate payload are
written here so the frontend can poll `GET /api/tailor/runs/{run_id}`
through the whole lifecycle.

ADDITIVE ONLY — creates one new table, touches nothing existing. The
JSON columns mirror `parse_runs.profile` / `raw_llm_output`: a payload
blob the worker fills in as it progresses.

Lifecycle: analyzing -> pending_questions -> generating -> done | error
(the questions stage is skipped straight to `generating` when the
analyze step finds no askable gaps).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_tailor_runs"
down_revision: str | None = "0017_profile_saved_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tailor_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Per-user ownership. The GET-status endpoint filters by
        # `user_id == current.id` AND `run_id` so one user can't poll
        # another user's tailor run by guessing the UUID. Nullable +
        # ondelete CASCADE to match parse_runs / candidates.
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("run_id", sa.String(length=64), nullable=False, unique=True),
        # Which job this run tailors against. Nullable so a future
        # pasted-JD path (no job row) can reuse the same table.
        sa.Column("job_id", sa.Integer(), nullable=True),
        # Snapshot of the JD text actually sent to the model, captured
        # so a bad tailor can be triaged without re-fetching the job.
        sa.Column("jd_text", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="analyzing",
        ),
        # The Analysis payload (questions / matched / gaps /
        # genuine_lacks / top_skills). Populated when the analyze
        # worker finishes; drives the questions stage.
        sa.Column("missing_skills_json", sa.JSON(), nullable=True),
        # The user's answers to the gap questions, keyed by question.
        sa.Column("user_answers_json", sa.JSON(), nullable=True),
        # The TailoredResume. Written incrementally (best-effort partial
        # parse) while status == generating, then the final validated
        # object on `done`. Preserved on `error` so a failed/partial
        # generation isn't lost.
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tailor_runs_run_id", "tailor_runs", ["run_id"])
    op.create_index("ix_tailor_runs_user_id", "tailor_runs", ["user_id"])
    op.create_index("ix_tailor_runs_started_at", "tailor_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_tailor_runs_started_at", table_name="tailor_runs")
    op.drop_index("ix_tailor_runs_user_id", table_name="tailor_runs")
    op.drop_index("ix_tailor_runs_run_id", table_name="tailor_runs")
    op.drop_table("tailor_runs")
