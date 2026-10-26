from datetime import date

from pydantic import BaseModel, Field


class StartLayerCycleRequest(BaseModel):
    # Day 1 of the cycle. May be in the past (for a batch already planted),
    # but not in the future.
    transplant_date: date
    plant_count: int = Field(gt=0, le=100)
    variety: str = Field(default="looseleaf lettuce", max_length=100)
    notes: str | None = Field(default=None, max_length=500)


class AbortLayerCycleRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class HarvestLayerCycleRequest(BaseModel):
    # The final photo chosen in the app. None = harvest without a final photo.
    image_id: int | None = None
    final_weight_g: float | None = Field(default=None, gt=0, le=100000)
    harvest_note: str | None = Field(default=None, max_length=500)