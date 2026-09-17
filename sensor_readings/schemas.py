from datetime import datetime

from pydantic import BaseModel


class CreateSensorReadingRequest(BaseModel):
    ph: float
    ec: float
    orp: float
    temperature: float


class SensorReadingResponse(BaseModel):
    id: int
    shelf_id: int
    recorded_at: datetime
    ph: float
    ec: float
    orp: float
    temperature: float