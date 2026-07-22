"""Request/response schemas for registration, login, and the current user."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterRequest(BaseModel):
    email: EmailStr
    # max_length=72 mirrors bcrypt's byte limit (see core.security) - this
    # catches the common case at the API boundary instead of silently
    # truncating a longer password.
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(min_length=1, max_length=255)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    auth_provider: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
