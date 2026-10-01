from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: int
    site_id: int
    shelf_id: int

    site_name: str
    shelf_name: str
    crop_type: str

    sensor_type: str
    alert_type: str

    value: float
    threshold_value: float

    status: str
    is_read: bool

    created_at: datetime
    resolved_at: Optional[datetime] = None