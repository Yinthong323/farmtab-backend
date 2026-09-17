from pydantic import BaseModel


class CreateSiteRequest(BaseModel):
    name: str
    description: str | None = None