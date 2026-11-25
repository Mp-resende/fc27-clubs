from pydantic import BaseModel

from app.api.schemas.championship import ClassificationOut
from app.api.schemas.player import PlayerStats


class MatchSummary(BaseModel):
    match_id: str
    played_at: str
    club_a_id: str
    club_b_id: str
    club_a_goals: int
    club_b_goals: int
    goals_for: int
    goals_against: int
    is_win: int
    is_draw: int
    is_loss: int
    dnf: int
    review_status: str = "valid"
    review_reason: str | None = None
    review_note: str | None = None
    result_source: str = "ea"
    ea_club_a_goals: int | None = None
    ea_club_b_goals: int | None = None
    is_counted: bool = True
    classification: ClassificationOut | None = None


class MatchDetail(BaseModel):
    match_id: str
    played_at: str
    platform: str
    match_type_code: str
    club_a_id: str
    club_b_id: str
    club_a_goals: int
    club_b_goals: int
    dnf: int
    winner_by_dnf: str | None = None
    season_id: str | None = None
    parser_version: str
    review_status: str = "valid"
    review_reason: str | None = None
    review_note: str | None = None
    result_source: str = "ea"
    ea_club_a_goals: int | None = None
    ea_club_b_goals: int | None = None
    is_counted: bool = True
    review: dict[str, object] | None = None
    classification: ClassificationOut | None = None
    players: list[PlayerStats] = []
