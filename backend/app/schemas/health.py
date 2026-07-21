"""Response schema for the health-check endpoint."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    environment: str
