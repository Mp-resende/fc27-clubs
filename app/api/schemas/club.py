from pydantic import BaseModel


class ClubInfo(BaseModel):
    club_id: str
    platform: str
    name: str
    team_id: str | None = None
    crest_asset_id: str | None = None
    crest_url: str | None = None
    stadium_name: str | None = None
    region_id: str | None = None
    first_seen_at: str
    updated_at: str
