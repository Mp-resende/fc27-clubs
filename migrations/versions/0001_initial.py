"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-30

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clubs",
        sa.Column("club_id", sa.String, nullable=False),
        sa.Column("platform", sa.String, nullable=False, server_default="common-gen5"),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("team_id", sa.String),
        sa.Column("crest_asset_id", sa.String),
        sa.Column("crest_url", sa.String),
        sa.Column("stadium_name", sa.String),
        sa.Column("region_id", sa.String),
        sa.Column("first_seen_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.PrimaryKeyConstraint("club_id", "platform"),
    )

    op.create_table(
        "matches",
        sa.Column("match_id", sa.String, primary_key=True, nullable=False),
        sa.Column("platform", sa.String, nullable=False, server_default="common-gen5"),
        sa.Column("match_type_code", sa.String, nullable=False),
        sa.Column("played_at", sa.String, nullable=False),
        sa.Column("season_id", sa.String),
        sa.Column("game_number", sa.String),
        sa.Column("duration_seconds", sa.Integer),
        sa.Column("dnf", sa.Integer, nullable=False, server_default="0"),
        sa.Column("winner_by_dnf", sa.String),
        sa.Column("club_a_id", sa.String, nullable=False),
        sa.Column("club_b_id", sa.String, nullable=False),
        sa.Column("club_a_goals", sa.Integer, nullable=False),
        sa.Column("club_b_goals", sa.Integer, nullable=False),
        sa.Column("raw_payload", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String, nullable=False),
        sa.Column("parser_version", sa.String, nullable=False),
        sa.Column("ingested_at", sa.String, nullable=False),
        sa.Column("first_seen_at", sa.String, nullable=False),
        sa.Column("last_seen_at", sa.String, nullable=False),
        sa.Column("seen_count", sa.Integer, nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_index("idx_matches_played_at", "matches", ["played_at"])

    op.create_table(
        "club_matches",
        sa.Column("club_id", sa.String, nullable=False),
        sa.Column("match_id", sa.String, nullable=False),
        sa.Column("platform", sa.String, nullable=False, server_default="common-gen5"),
        sa.Column("goals_for", sa.Integer, nullable=False),
        sa.Column("goals_against", sa.Integer, nullable=False),
        sa.Column("is_win", sa.Integer, nullable=False),
        sa.Column("is_draw", sa.Integer, nullable=False),
        sa.Column("is_loss", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("club_id", "match_id"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"]),
    )
    op.create_index("idx_club_matches", "club_matches", ["club_id", "match_id"])

    op.create_table(
        "match_team_stats",
        sa.Column("match_id", sa.String, nullable=False),
        sa.Column("club_id", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False, server_default="ea_aggregate"),
        sa.Column("goals", sa.Integer),
        sa.Column("shots", sa.Integer),
        sa.Column("shots_on_target", sa.Integer),
        sa.Column("passes_made", sa.Integer),
        sa.Column("pass_attempts", sa.Integer),
        sa.Column("tackles_made", sa.Integer),
        sa.Column("tackle_attempts", sa.Integer),
        sa.Column("saves", sa.Integer),
        sa.Column("goals_conceded", sa.Integer),
        sa.Column("fouls", sa.Integer),
        sa.Column("corners", sa.Integer),
        sa.Column("possession_pct", sa.Float),
        sa.Column("raw_aggregate", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("match_id", "club_id"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"]),
    )

    op.create_table(
        "players",
        sa.Column("player_key", sa.String, primary_key=True, nullable=False),
        sa.Column("ea_player_id", sa.String),
        sa.Column("platform", sa.String, nullable=False, server_default="common-gen5"),
        sa.Column("first_seen_at", sa.String, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False),
        sa.PrimaryKeyConstraint("player_key"),
    )
    op.create_index(
        "uidx_players_ea",
        "players",
        ["ea_player_id"],
        unique=True,
        postgresql_where=sa.text("ea_player_id IS NOT NULL"),
    )

    op.create_table(
        "player_names",
        sa.Column("player_key", sa.String, nullable=False),
        sa.Column("playername", sa.String, nullable=False),
        sa.Column("first_seen_at", sa.String, nullable=False),
        sa.Column("last_seen_at", sa.String, nullable=False),
        sa.PrimaryKeyConstraint("player_key", "playername"),
        sa.ForeignKeyConstraint(["player_key"], ["players.player_key"]),
    )

    op.create_table(
        "player_match_stats",
        sa.Column("match_id", sa.String, nullable=False),
        sa.Column("club_id", sa.String, nullable=False),
        sa.Column("player_key", sa.String, nullable=False),
        sa.Column("playername", sa.String, nullable=False),
        sa.Column("position", sa.String),
        sa.Column("rating", sa.Float),
        sa.Column("seconds_played", sa.Integer),
        sa.Column("game_time", sa.Integer),
        sa.Column("goals", sa.Integer, server_default="0"),
        sa.Column("assists", sa.Integer, server_default="0"),
        sa.Column("shots", sa.Integer, server_default="0"),
        sa.Column("passes_made", sa.Integer, server_default="0"),
        sa.Column("pass_attempts", sa.Integer, server_default="0"),
        sa.Column("tackles_made", sa.Integer, server_default="0"),
        sa.Column("tackle_attempts", sa.Integer, server_default="0"),
        sa.Column("saves", sa.Integer, server_default="0"),
        sa.Column("goals_conceded", sa.Integer, server_default="0"),
        sa.Column("clean_sheets_any", sa.Integer, server_default="0"),
        sa.Column("clean_sheets_def", sa.Integer, server_default="0"),
        sa.Column("clean_sheets_gk", sa.Integer, server_default="0"),
        sa.Column("is_mom", sa.Integer, server_default="0"),
        sa.Column("red_cards", sa.Integer, server_default="0"),
        sa.Column("pro_overall", sa.Integer),
        sa.Column("archetype_id", sa.String),
        sa.Column("raw_player", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("match_id", "player_key"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"]),
        sa.ForeignKeyConstraint(["player_key"], ["players.player_key"]),
    )
    op.create_index("idx_pms_player", "player_match_stats", ["player_key"])
    op.create_index("idx_pms_club_match", "player_match_stats", ["club_id", "match_id"])

    op.create_table(
        "player_match_events",
        sa.Column("match_id", sa.String, nullable=False),
        sa.Column("player_key", sa.String, nullable=False),
        sa.Column("event_id", sa.Integer, nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("bucket", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("match_id", "player_key", "event_id", "bucket"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"]),
        sa.ForeignKeyConstraint(["player_key"], ["players.player_key"]),
    )

    op.create_table(
        "player_match_metrics",
        sa.Column("match_id", sa.String, nullable=False),
        sa.Column("player_key", sa.String, nullable=False),
        sa.Column("passes_completed", sa.Integer),
        sa.Column("passes_failed", sa.Integer),
        sa.Column("passes_completed_forward", sa.Integer),
        sa.Column("passes_completed_backward", sa.Integer),
        sa.Column("passes_completed_sideways", sa.Integer),
        sa.Column("passes_completed_short", sa.Integer),
        sa.Column("passes_completed_medium", sa.Integer),
        sa.Column("passes_completed_long", sa.Integer),
        sa.Column("passes_completed_cross", sa.Integer),
        sa.Column("passes_failed_forward", sa.Integer),
        sa.Column("passes_failed_backward", sa.Integer),
        sa.Column("passes_failed_sideways", sa.Integer),
        sa.Column("passes_failed_short", sa.Integer),
        sa.Column("passes_failed_medium", sa.Integer),
        sa.Column("passes_failed_long", sa.Integer),
        sa.Column("first_time_passes", sa.Integer),
        sa.Column("switches_of_play", sa.Integer),
        sa.Column("flair_passes", sa.Integer),
        sa.Column("through_passes", sa.Integer),
        sa.Column("offside_passes", sa.Integer),
        sa.Column("shots_on_target", sa.Integer),
        sa.Column("shots_off_target", sa.Integer),
        sa.Column("shots_on_target_inside_box", sa.Integer),
        sa.Column("shots_on_target_outside_box", sa.Integer),
        sa.Column("shots_off_target_inside_box", sa.Integer),
        sa.Column("shots_off_target_outside_box", sa.Integer),
        sa.Column("shots_saved", sa.Integer),
        sa.Column("goals_by_events", sa.Integer),
        sa.Column("goals_first_time", sa.Integer),
        sa.Column("goals_weak_foot", sa.Integer),
        sa.Column("goals_off_post", sa.Integer),
        sa.Column("second_assists", sa.Integer),
        sa.Column("through_ball_assists", sa.Integer),
        sa.Column("dribbles_completed", sa.Integer),
        sa.Column("dribbles_carried", sa.Integer),
        sa.Column("dribbles_beat", sa.Integer),
        sa.Column("dribbles_skill_move_beat", sa.Integer),
        sa.Column("tackles_won", sa.Integer),
        sa.Column("tackles_lost", sa.Integer),
        sa.Column("standing_tackles_won", sa.Integer),
        sa.Column("sliding_tackles_won", sa.Integer),
        sa.Column("clean_tackles", sa.Integer),
        sa.Column("dangerous_tackles", sa.Integer),
        sa.Column("interceptions", sa.Integer),
        sa.Column("opponents_dispossessed", sa.Integer),
        sa.Column("crosses_blocked", sa.Integer),
        sa.Column("aerial_duels_won_attacking", sa.Integer),
        sa.Column("aerial_duels_won_defending", sa.Integer),
        sa.Column("good_direction_saves", sa.Integer),
        sa.Column("possession_lost_defensive_third", sa.Integer),
        sa.Column("possession_lost_middle_third", sa.Integer),
        sa.Column("possession_lost_attacking_third", sa.Integer),
        sa.Column("possession_won_defensive_third", sa.Integer),
        sa.Column("possession_won_middle_third", sa.Integer),
        sa.Column("possession_won_attacking_third", sa.Integer),
        sa.Column("fouls_conceded", sa.Integer),
        sa.Column("fouls_conceded_defensive_third", sa.Integer),
        sa.Column("fouls_won", sa.Integer),
        sa.Column("yellow_cards", sa.Integer),
        sa.Column("yellow_cards_immediate", sa.Integer),
        sa.Column("yellow_cards_delayed", sa.Integer),
        sa.Column("penalties_conceded", sa.Integer),
        sa.Column("corners_conceded", sa.Integer),
        sa.Column("corners_taken", sa.Integer),
        sa.Column("in_position", sa.Integer),
        sa.Column("out_of_position", sa.Integer),
        sa.Column("oop_severity_1", sa.Integer),
        sa.Column("oop_severity_2", sa.Integer),
        sa.Column("oop_severity_3", sa.Integer),
        sa.Column("oop_severity_4", sa.Integer),
        sa.Column("oop_severity_5", sa.Integer),
        sa.Column("feedback_use_the_ball", sa.Integer),
        sa.Column("feedback_pick_your_pass", sa.Integer),
        sa.Column("feedback_no_good_option", sa.Integer),
        sa.Column("feedback_good_option_taken", sa.Integer),
        sa.Column("feedback_best_option_taken", sa.Integer),
        sa.Column("feedback_should_have_passed_elsewhere", sa.Integer),
        sa.Column("feedback_chose_to_pass", sa.Integer),
        sa.Column("metrics_version", sa.String, nullable=False, server_default="events-3.0"),
        sa.PrimaryKeyConstraint("match_id", "player_key"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"]),
        sa.ForeignKeyConstraint(["player_key"], ["players.player_key"]),
    )

    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("club_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("started_at", sa.String, nullable=False),
        sa.Column("finished_at", sa.String),
        sa.Column("http_status", sa.Integer),
        sa.Column("matches_returned", sa.Integer),
        sa.Column("matches_inserted", sa.Integer, server_default="0"),
        sa.Column("matches_updated", sa.Integer, server_default="0"),
        sa.Column("error", sa.Text),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ingest_runs_club", "ingest_runs", ["club_id"])


def downgrade() -> None:
    op.drop_table("ingest_runs")
    op.drop_table("player_match_metrics")
    op.drop_table("player_match_events")
    op.drop_table("player_match_stats")
    op.drop_table("player_names")
    op.drop_table("players")
    op.drop_table("match_team_stats")
    op.drop_table("club_matches")
    op.drop_table("matches")
    op.drop_table("clubs")
