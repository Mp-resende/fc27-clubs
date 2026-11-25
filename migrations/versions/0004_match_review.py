"""add match review, evidence and ignored-result support

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01

EA returns an "official" 3x0 when a club abandons a match and may also list a
match that both clubs abandoned right after kick-off. Those scorelines are not
the real result, so the application needs a review layer:

* ``matches.review_status`` tells whether a result is valid, waiting for
  review, ignored or corrected;
* ``matches.ea_club_*_goals`` preserves the original EA scoreline;
* ``club_matches.is_ignored`` makes aggregates simple to filter;
* ``match_evidence`` stores screenshots uploaded to prove/correct a result.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column(
            "review_status",
            sa.String,
            nullable=False,
            server_default="valid",
        ),
    )
    op.add_column("matches", sa.Column("review_reason", sa.String))
    op.add_column("matches", sa.Column("review_note", sa.Text))
    op.add_column("matches", sa.Column("reviewed_at", sa.String))
    op.add_column(
        "matches",
        sa.Column(
            "result_source",
            sa.String,
            nullable=False,
            server_default="ea",
        ),
    )
    op.add_column("matches", sa.Column("ea_club_a_goals", sa.Integer))
    op.add_column("matches", sa.Column("ea_club_b_goals", sa.Integer))

    op.add_column(
        "club_matches",
        sa.Column("is_ignored", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_club_matches_is_ignored",
        "club_matches",
        ["club_id", "is_ignored"],
    )

    op.create_table(
        "match_evidence",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.String, nullable=False),
        sa.Column("stored_path", sa.String, nullable=False),
        sa.Column("original_name", sa.String),
        sa.Column("content_type", sa.String),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("sha256", sa.String),
        sa.Column("extractor", sa.String, nullable=False, server_default="none"),
        sa.Column("extracted_json", sa.Text),
        sa.Column("created_at", sa.String, nullable=False),
        sa.ForeignKeyConstraint(
            ["match_id"], ["matches.match_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_match_evidence_match_id",
        "match_evidence",
        ["match_id"],
    )

    # Existing rows were accepted as-is by the old version; keep them valid but
    # remember the original EA scoreline so future corrections are auditable.
    op.execute(
        """
        UPDATE matches
        SET ea_club_a_goals = club_a_goals,
            ea_club_b_goals = club_b_goals
        WHERE ea_club_a_goals IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_match_evidence_match_id", table_name="match_evidence")
    op.drop_table("match_evidence")
    op.drop_index("ix_club_matches_is_ignored", table_name="club_matches")
    op.drop_column("club_matches", "is_ignored")
    op.drop_column("matches", "ea_club_b_goals")
    op.drop_column("matches", "ea_club_a_goals")
    op.drop_column("matches", "result_source")
    op.drop_column("matches", "reviewed_at")
    op.drop_column("matches", "review_note")
    op.drop_column("matches", "review_reason")
    op.drop_column("matches", "review_status")
