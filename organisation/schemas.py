from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class CreateOrganisationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    website: Optional[HttpUrl] = None
    phone_number: Optional[str] = Field(
        default=None,
        max_length=30,
    )
    organisation_type: str