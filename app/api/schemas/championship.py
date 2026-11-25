from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChampionshipFormat = Literal["league", "knockout", "groups", "mixed", "other"]
ChampionshipStatus = Literal["active", "finished", "archived"]
Leg = Literal["ida", "volta", "unico"]


class ChampionshipCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    format: ChampionshipFormat = "league"
    status: ChampionshipStatus = "active"
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    color: str | None = Field(default="#3b82f6", max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    external_url: str | None = Field(default=None, max_length=500)
    extra: dict[str, object] = Field(default_factory=dict)


class ChampionshipUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    format: ChampionshipFormat | None = None
    status: ChampionshipStatus | None = None
    start_date: str | None = Field(default=None, max_length=10)
    end_date: str | None = Field(default=None, max_length=10)
    color: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    external_url: str | None = Field(default=None, max_length=500)
    extra: dict[str, object] | None = None


class ClassificationCreate(BaseModel):
    championship_id: int
    stage: str | None = Field(default=None, max_length=80)
    round: str | None = Field(default=None, max_length=80)
    leg: Leg | None = None
    group_name: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)
    tags: list[str] | str | None = None


class BulkClassificationCreate(ClassificationCreate):
    match_ids: list[str] = Field(min_length=1, max_length=500)


class ClassificationOut(BaseModel):
    match_id: str
    championship_id: int
    championship_name: str
    championship_color: str | None = None
    championship_status: str | None = None
    stage: str | None = None
    round: str | None = None
    leg: str | None = None
    group_name: str | None = None
    notes: str | None = None
    tags: list[str] = []


class CampaignOut(BaseModel):
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    goal_diff: int = 0
    points: int = 0
    points_per_game: float = 0.0
    points_pct: float = 0.0
    win_rate: float = 0.0


class ChampionshipOut(BaseModel):
    id: int
    name: str
    format: str
    status: str
    start_date: str | None = None
    end_date: str | None = None
    color: str | None = None
    description: str | None = None
    external_url: str | None = None
    extra: dict[str, object] = {}
    created_at: str
    updated_at: str
    matches: int = 0
    campaign: CampaignOut = Field(default_factory=CampaignOut)


class BulkClassificationResult(BaseModel):
    championship_id: int
    classified: int
    skipped: list[str] = []
