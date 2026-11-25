from pydantic import BaseModel


class IngestRunInfo(BaseModel):
    id: int
    club_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    matches_returned: int | None = None
    matches_inserted: int = 0
    matches_updated: int = 0
    error: str | None = None
