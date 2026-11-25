"""add championships and match classification

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31

EA only reports friendlies, but the user plays them inside private competitions.
``championships`` stores those competitions and ``match_classifications`` links a
match to at most one championship (``match_id`` is the primary key).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "championships",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("format", sa.String, nullable=False, server_default="league"),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("start_date", sa.String),
        sa.Column("end_date", sa.String),
        sa.Column("color", sa.String, server_default="#3b82f6"),
        sa.Column("description", sa.Text),
        sa.Column("external_url", sa.String),
        sa.Column("extra", sa.Text),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )

    op.create_table(
        "match_classifications",
        sa.Column("match_id", sa.String, nullable=False),
        sa.Column("championship_id", sa.Integer, nullable=False),
        sa.Column("stage", sa.String),
        sa.Column("round", sa.String),
        sa.Column("leg", sa.String),
        sa.Column("group_name", sa.String),
        sa.Column("notes", sa.Text),
        sa.Column("tags", sa.Text),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.match_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["championship_id"], ["championships.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_index(
        "ix_match_classifications_championship_id",
        "match_classifications",
        ["championship_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_match_classifications_championship_id", table_name="match_classifications")
    op.drop_table("match_classifications")
    op.drop_table("championships")
