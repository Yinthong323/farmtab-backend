from pydantic import BaseModel


class CreateShelfRequest(BaseModel):
    name: str
    description: str | None = None
    crop_type: str
    device_serial_number: str


class UpdateShelfRequest(BaseModel):
    name: str
    description: str | None = None
    crop_type: str
    device_serial_number: str

class UpdateShelfThresholdRequest(BaseModel):
    ph_min: float
    ph_max: float

    ec_min: float
    ec_max: float

    orp_min: float
    orp_max: float

    temperature_min: float
    temperature_max: float