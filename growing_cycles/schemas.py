from datetime import date, datetime

from pydantic import BaseModel, Field


class CreateGrowingCycleRequest(BaseModel):
    start_date: date
    target_harvest_days: int = Field(gt=0)


class GrowingCycleResponse(BaseModel):
    id: int
    shelf_id: int
    cycle_number: int
    crop_type: str
    start_date: date
    target_harvest_days: int
    target_harvest_date: date
    actual_harvest_date: date | None
    actual_growth_days: int | None
    status: str
    stop_reason: str | None
    created_at: datetime
    updated_at: datetime

class StopGrowingCycleRequest(BaseModel):
    stop_reason: str | None = None