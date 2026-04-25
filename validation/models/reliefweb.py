from datetime import date
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ReliefWebEvent(BaseModel):
    source:             str          = "reliefweb"
    source_event_id:    str
    event_name:         Optional[str]   = None
    main_cause:         Optional[str]   = None
    date_start:         date
    date_end:           Optional[date]  = None
    country:            Optional[str]   = None
    latitude:           Optional[float] = None
    longitude:          Optional[float] = None
    deaths:             Optional[int]   = Field(default=0)
    displaced:          Optional[int]   = Field(default=0)
    affected:           Optional[int]   = Field(default=0)
    severity:           Optional[float] = None
    flood_impact_index: Optional[float] = None
    glide_number:       Optional[str]   = None
    url:                Optional[str]   = None
    h3_index:           Optional[str]   = None
    river_basin:        Optional[str]   = None

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, value):
        if value is not None and not (-90 <= value <= 90):
            raise ValueError("latitude out of range")
        return value

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, value):
        if value is not None and not (-180 <= value <= 180):
            raise ValueError("longitude out of range")
        return value

    @field_validator("deaths", "displaced", "affected")
    @classmethod
    def validate_non_negative(cls, value):
        if value is not None and value < 0:
            raise ValueError("value must be non-negative")
        return value