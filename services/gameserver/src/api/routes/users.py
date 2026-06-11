from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime, UTC

from src.core.database import get_db
from src.auth.dependencies import get_current_admin_user, admin_or_options
from src.models.user import User
from src.models.admin_credentials import AdminCredentials
from src.schemas.user import User as UserSchema, UserCreate, UserUpdate, AdminCreate
from src.services.user_service import get_user, get_users
from src.core.security import get_password_hash

router = APIRouter()


@router.get("/", response_model=List[UserSchema])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Retrieve users.
    """
    users = get_users(db, skip=skip, limit=limit)
    return users

@router.options("/")
async def options_users():
    """
    Handle preflight CORS requests for the users endpoint.
    This is especially important for GitHub Codespaces.
    """
    return {
        "status": "ok"
    }


@router.post("/", response_model=UserSchema)
async def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Create new user.
    """
    # Check if username already exists
    db_user = db.query(User).filter(User.username == user_data.username).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email already exists
    if user_data.email:
        db_user = db.query(User).filter(User.email == user_data.email).first()
        if db_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
    
    # Create new user
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        is_active=True,
        is_admin=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC)
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return new_user


@router.post("/admin", response_model=UserSchema)
async def create_admin_user(
    admin_data: AdminCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Create new admin user.
    """
    # Check if username already exists
    db_user = db.query(User).filter(User.username == admin_data.username).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    # Check if email already exists
    if admin_data.email:
        db_user = db.query(User).filter(User.email == admin_data.email).first()
        if db_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
    
    # Create new user
    new_user = User(
        username=admin_data.username,
        email=admin_data.email,
        is_active=True,
        is_admin=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC)
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create admin credentials
    password_hash = get_password_hash(admin_data.password)
    admin_creds = AdminCredentials(
        user_id=new_user.id,
        password_hash=password_hash
    )
    
    db.add(admin_creds)
    db.commit()
    
    return new_user


@router.get("/{user_id}", response_model=UserSchema)
async def read_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Get a specific user by id.
    """
    db_user = get_user(db, str(user_id))
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return db_user


@router.put("/{user_id}", response_model=UserSchema)
async def update_user(
    user_id: UUID,
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Update a user.
    """
    db_user = get_user(db, str(user_id))
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Update attributes that are provided
    if user_data.username is not None:
        # Check if username already exists
        existing_user = db.query(User).filter(
            User.username == user_data.username,
            User.id != user_id
        ).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken"
            )
        db_user.username = user_data.username
    
    if user_data.email is not None:
        # Check if email already exists
        existing_user = db.query(User).filter(
            User.email == user_data.email,
            User.id != user_id
        ).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        db_user.email = user_data.email
    
    if user_data.is_active is not None:
        db_user.is_active = user_data.is_active
    
    db_user.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(db_user)
    
    return db_user


@router.delete("/{user_id}", response_model=UserSchema)
async def delete_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Delete a user (soft delete).
    """
    db_user = get_user(db, str(user_id))
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent deleting yourself
    if str(db_user.id) == str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    # Soft delete
    db_user.deleted = True
    db_user.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(db_user)
    
    return db_user


@router.put("/{user_id}/password", response_model=dict)
async def reset_admin_password(
    user_id: UUID,
    password: str = Body(..., min_length=8),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Reset password for an admin user.
    """
    db_user = get_user(db, str(user_id))
    if db_user is None or not db_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin user not found"
        )
    
    # Get admin credentials
    admin_creds = db.query(AdminCredentials).filter(
        AdminCredentials.user_id == user_id
    ).first()
    
    if not admin_creds:
        # Create admin credentials if they don't exist
        admin_creds = AdminCredentials(
            user_id=user_id,
            password_hash=get_password_hash(password)
        )
        db.add(admin_creds)
    else:
        # Update password hash
        admin_creds.password_hash = get_password_hash(password)
    
    db.commit()
    
    return {"detail": "Password updated successfully"}