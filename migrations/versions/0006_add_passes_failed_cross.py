"""add passes_failed_cross metric column

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03

Event id 37 is ``passes_failed_cross``; league payloads carry it and the
PlayerMatchMetrics table had no column for it, so any league match with a
failed cross aborted the whole match ingest.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {c["name"] for c in inspector.get_columns("player_match_metrics")}
    if "passes_failed_cross" not in existing:
        op.add_column("player_match_metrics", sa.Column("passes_failed_cross", sa.Integer))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {c["name"] for c in inspector.get_columns("player_match_metrics")}
    if "passes_failed_cross" in existing:
        op.drop_column("player_match_metrics", "passes_failed_cross")
