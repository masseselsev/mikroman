"""Schemas for authentication and session management."""
from typing import Optional

from pydantic import BaseModel, Field


class AuthStatusResponse(BaseModel):
    auth_enabled: bool = Field(..., description="Whether authentication is currently enforced")
    authenticated: bool = Field(..., description="Whether the requesting client has a valid session or token")
    needs_setup: bool = Field(..., description="Whether an admin password has not yet been configured")
    username: Optional[str] = Field(None, description="Username if authenticated")


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1, description="Admin password")


class SetupRequest(BaseModel):
    password: str = Field(..., min_length=8, description="New admin password (minimum 8 characters)")


class LoginResponseData(BaseModel):
    username: str = Field("admin", description="Authenticated user")
    expires_in: int = Field(..., description="Session duration in seconds")
