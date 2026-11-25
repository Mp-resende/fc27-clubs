from pydantic import BaseModel


class PlayerMetrics(BaseModel):
    passes_completed: int | None = None
    passes_failed: int | None = None
    passes_completed_forward: int | None = None
    passes_completed_backward: int | None = None
    passes_completed_sideways: int | None = None
    shots_on_target: int | None = None
    shots_off_target: int | None = None
    tackles_won: int | None = None
    tackles_lost: int | None = None
    interceptions: int | None = None
    dribbles_completed: int | None = None
    fouls_conceded: int | None = None
    yellow_cards: int | None = None
    in_position: int | None = None
    out_of_position: int | None = None
    possession_won_defensive_third: int | None = None
    possession_won_middle_third: int | None = None
    possession_won_attacking_third: int | None = None
    possession_lost_defensive_third: int | None = None
    possession_lost_middle_third: int | None = None
    possession_lost_attacking_third: int | None = None


class PlayerStats(BaseModel):
    player_key: str
    club_id: str
    playername: str
    position: str | None = None
    rating: float | None = None
    seconds_played: int | None = None
    goals: int = 0
    assists: int = 0
    shots: int = 0
    passes_made: int = 0
    saves: int = 0
    is_mom: int = 0
    metrics: dict[str, object] = {}
