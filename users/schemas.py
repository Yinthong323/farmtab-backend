from pydantic import BaseModel
from typing import Optional


class UserProfileResponse(BaseModel):
    id: int
    username: str
    email: str
    is_verified: bool
    created_at: str
    profile_image: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    username: str