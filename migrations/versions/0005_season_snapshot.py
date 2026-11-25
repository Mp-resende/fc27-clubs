"""add EA season snapshot and member stats

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02

The dashboard now consumes EA's season endpoints (``clubs/overallStats``,
``members/stats``, ``members/career/stats``). Those payloads are club-wide and
independent from the local match sample, so they live in their own tables:

* ``club_overall_stats``: one snapshot per club (record, goals, skill rating,
  tiers, streaks, last results);
* ``club_members`` / ``club_member_careers``: per-member season and career
  totals, linked to ``players.player_key`` by name;
* ``player_aliases``: manual name links for members EA spells differently.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "club_overall_stats",
        sa.Column("club_id", sa.String, primary_key=True),
        sa.Column(
            "platform", sa.String, primary_key=True, server_default="common-gen5"
        ),
        sa.Column("games_played", sa.Integer),
        sa.Column("wins", sa.Integer),
        sa.Column("ties", sa.Integer),
        sa.Column("losses", sa.Integer),
        sa.Column("goals", sa.Integer),
        sa.Column("goals_against", sa.Integer),
        sa.Column("promotions", sa.Integer),
        sa.Column("relegations", sa.Integer),
        sa.Column("best_division", sa.Integer),
        sa.Column("best_finish_group", sa.Integer),
        sa.Column("skill_rating", sa.Integer),
        sa.Column("reputation_tier", sa.Integer),
        sa.Column("win_streak", sa.Integer),
        sa.Column("unbeaten_streak", sa.Integer),
        sa.Column("league_appearances", sa.Integer),
        sa.Column("playoff_games", sa.Integer),
        sa.Column("division_finishes", sa.Text),
        sa.Column("last_results", sa.Text),
        sa.Column("raw_json", sa.Text, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
    )

    op.create_table(
        "club_members",
        sa.Column("club_id", sa.String, primary_key=True),
        sa.Column(
            "platform", sa.String, primary_key=True, server_default="common-gen5"
        ),
        sa.Column("name", sa.String, primary_key=True),
        sa.Column("player_key", sa.String),
        sa.Column("pro_name", sa.String),
        sa.Column("position", sa.String),
        sa.Column("pro_pos", sa.Integer),
        sa.Column("overall", sa.Integer),
        sa.Column("games_played", sa.Integer),
        sa.Column("win_rate", sa.Integer),
        sa.Column("goals", sa.Integer),
        sa.Column("assists", sa.Integer),
        sa.Column("passes_made", sa.Integer),
        sa.Column("pass_success_rate", sa.Integer),
        sa.Column("shot_success_rate", sa.Integer),
        sa.Column("tackle_success_rate", sa.Integer),
        sa.Column("tackles_made", sa.Integer),
        sa.Column("rating_avg", sa.Float),
        sa.Column("man_of_the_match", sa.Integer),
        sa.Column("red_cards", sa.Integer),
        sa.Column("clean_sheets_def", sa.Integer),
        sa.Column("clean_sheets_gk", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("nationality", sa.String),
        sa.Column("prev_goals", sa.Text),
        sa.Column("raw_json", sa.Text, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.ForeignKeyConstraint(["player_key"], ["players.player_key"]),
    )
    op.create_index("ix_club_members_player_key", "club_members", ["player_key"])

    op.create_table(
        "club_member_careers",
        sa.Column("club_id", sa.String, primary_key=True),
        sa.Column(
            "platform", sa.String, primary_key=True, server_default="common-gen5"
        ),
        sa.Column("name", sa.String, primary_key=True),
        sa.Column("player_key", sa.String),
        sa.Column("position", sa.String),
        sa.Column("pro_pos", sa.Integer),
        sa.Column("games_played", sa.Integer),
        sa.Column("goals", sa.Integer),
        sa.Column("assists", sa.Integer),
        sa.Column("man_of_the_match", sa.Integer),
        sa.Column("rating_avg", sa.Float),
        sa.Column("prev_goals", sa.Integer),
        sa.Column("raw_json", sa.Text, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.ForeignKeyConstraint(["player_key"], ["players.player_key"]),
    )
    op.create_index(
        "ix_club_member_careers_player_key", "club_member_careers", ["player_key"]
    )

    op.create_table(
        "player_aliases",
        sa.Column("alias", sa.String, primary_key=True),
        sa.Column("display_alias", sa.String),
        sa.Column("player_key", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.ForeignKeyConstraint(["player_key"], ["players.player_key"]),
    )
    op.create_index("ix_player_aliases_player_key", "player_aliases", ["player_key"])

    op.create_index(
        "ix_matches_type_played_at",
        "matches",
        ["match_type_code", "played_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_matches_type_played_at", table_name="matches")
    op.drop_index("ix_player_aliases_player_key", table_name="player_aliases")
    op.drop_table("player_aliases")
    op.drop_index(
        "ix_club_member_careers_player_key", table_name="club_member_careers"
    )
    op.drop_table("club_member_careers")
    op.drop_index("ix_club_members_player_key", table_name="club_members")
    op.drop_table("club_members")
    op.drop_table("club_overall_stats")
