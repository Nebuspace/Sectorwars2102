from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, UUID4, ConfigDict


# Shared properties
class UserBase(BaseModel):
    username: str
    email: Optional[str] = None  # Changed from EmailStr to str to avoid validation issues


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: Optional[str] = None


# Properties to receive via API on update
class UserUpdate(UserBase):
    username: Optional[str] = None
    is_active: Optional[bool] = None


# Properties shared by models stored in DB
class UserInDBBase(UserBase):
    id: UUID4
    is_active: bool
    is_admin: bool
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime] = None
    deleted: bool = False

    model_config = ConfigDict(from_attributes=True)


# Properties to return to client
class User(UserInDBBase):
    pass


# Properties stored in DB but not returned to the client
class UserInDB(UserInDBBase):
    pass


# Admin user creation
class AdminCreate(UserCreate):
    password: str = Field(..., min_length=8)
    is_admin: bool = True


# Admin response schema for user data
class UserAdminResponse(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    is_active: bool
    is_admin: bool
    created_at: str
    last_login: Optional[str] = None
    verified: bool