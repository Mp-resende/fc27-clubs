"""add append-only raw payload archive

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31

Stores every distinct raw EA payload ever captured, so the parsed data can be
rebuilt later even if the parser or schema changes. Existing match payloads are
backfilled from the ``matches`` table.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_payload_archive",
        sa.Column("match_id", sa.String, primary_key=True, nullable=False),
        sa.Column("payload_hash", sa.String, primary_key=True, nullable=False),
        sa.Column("parser_version", sa.String, nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("captured_at", sa.String, nullable=False),
        sa.Column("first_seen_at", sa.String, nullable=False),
    )

    # Backfill payloads already stored in matches (one row per match hash).
    op.execute(
        """
        INSERT OR IGNORE INTO match_payload_archive
            (match_id, payload_hash, parser_version, payload_json, captured_at, first_seen_at)
        SELECT match_id, payload_hash, parser_version, raw_payload, ingested_at, first_seen_at
        FROM matches
        """
    )


def downgrade() -> None:
    op.drop_table("match_payload_archive")
