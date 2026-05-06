from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ResponsibilityAreaBase(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    is_active: bool = True


class ResponsibilityAreaCreate(ResponsibilityAreaBase):
    pass


class ResponsibilityAreaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    is_active: bool | None = None


class ResponsibilityAreaUserRead(BaseModel):
    id: int
    username: str
    full_name: str
    email: str

    model_config = ConfigDict(from_attributes=True)


class ResponsibilityAreaDeviceRead(BaseModel):
    id: int
    code: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class ResponsibilityAreaRegionRead(BaseModel):
    id: int
    code: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class ResponsibilityAreaLineRead(BaseModel):
    id: int
    code: str
    name: str
    region_id: int

    model_config = ConfigDict(from_attributes=True)


class ResponsibilityAreaRead(ResponsibilityAreaBase):
    id: int
    created_at: datetime
    user_count: int = 0
    device_count: int = 0
    region_count: int = 0
    line_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ResponsibilityAreaDetail(ResponsibilityAreaRead):
    users: list[ResponsibilityAreaUserRead] = []
    devices: list[ResponsibilityAreaDeviceRead] = []
    regions: list[ResponsibilityAreaRegionRead] = []
    lines: list[ResponsibilityAreaLineRead] = []
