"""
app/keywords/schemas.py
"""
from datetime import datetime
from enum import Enum
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class KeywordType(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    BRAND = "brand"


class KeywordBase(BaseModel):
    keyword: str = Field(min_length=1, max_length=255)
    keyword_type: KeywordType


class KeywordCreate(KeywordBase):
    project_id: UUID


class KeywordUpdate(BaseModel):
    keyword: Optional[str] = Field(default=None, min_length=1, max_length=255)
    keyword_type: Optional[KeywordType] = None


class KeywordResponse(KeywordBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    created_at: datetime
