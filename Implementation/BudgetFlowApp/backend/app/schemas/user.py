from decimal import Decimal
from typing import Optional
import uuid

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator


class UserBase(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=100)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)


class UserOut(UserBase):
    id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class UserProfileRead(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    preferred_currency: str
    monthly_income_goal: Optional[Decimal] = None
    display_title: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class UserProfileUpdate(BaseModel):
    name: Optional[str] = None
    preferred_currency: Optional[str] = None
    monthly_income_goal: Optional[Decimal] = None
    display_title: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("name must not be blank")
        return v.strip() if v is not None else v

    @field_validator("preferred_currency")
    @classmethod
    def currency_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not (3 <= len(v) <= 10):
            raise ValueError("preferred_currency must be 3–10 characters")
        return v

    @field_validator("monthly_income_goal")
    @classmethod
    def income_goal_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("monthly_income_goal must be >= 0")
        return v
