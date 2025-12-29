"""Currency-related Pydantic schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CurrencyCreate(BaseModel):
    """Schema for creating a currency."""

    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=3, max_length=3)
    symbol: str = Field(..., min_length=1, max_length=10)

    @field_validator("code")
    @classmethod
    def code_uppercase(cls, v: str) -> str:
        """Ensure code is uppercase."""
        return v.upper()


class CurrencyUpdate(BaseModel):
    """Schema for updating a currency.

    Note: code is immutable after creation.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    symbol: Optional[str] = Field(None, min_length=1, max_length=10)
    is_active: Optional[bool] = None


class CurrencyResponse(BaseModel):
    """Schema for currency response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    symbol: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
