"""Pydantic schemas shared across layers (validation of structured data at boundaries).

These are framework-independent so a future REST API or mobile backend can reuse them.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MedicineExtractionOutput(BaseModel):
    """Raw structured output expected from the vision model (before normalisation)."""
    model_config = ConfigDict(extra="allow")
    medicine_name: Optional[str] = None
    generic_name: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    manufacturing_date: Optional[str] = None
    expiry_date: Optional[str] = None
    confidence: Optional[dict] = None


class MedicineExtractionResult(BaseModel):
    """Normalised candidate fields shown to the user – always requires manual review."""
    medicine_name: Optional[str] = None
    generic_name: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_number: Optional[str] = None
    manufacturing_date: Optional[date] = None
    expiry_date: Optional[date] = None
    confidence: dict = Field(default_factory=dict)
    requires_manual_review: bool = True


class RiskComponents(BaseModel):
    t_score: float = Field(ge=0, le=100)
    q_score: float = Field(ge=0, le=100)
    d_score: float = Field(ge=0, le=100)
    c_score: float = Field(ge=0, le=100)
    risk_score: float = Field(ge=0, le=100)
    risk_level: str
    expiry_category: str
    algorithm_version: str
