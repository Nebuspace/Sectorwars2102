from datetime import datetime, UTC
from typing import List, Optional
from sqlalchemy.orm import Session

from src.models.user import User
from src.models.admin_credentials import AdminCredentials
from src.models.player_credentials import PlayerCredentials
from src.core.security import verify_password


def get_user(db: Session, user_id: str) -> Optional[User]:
    """Get a user by ID."""
    return db.query(User).filter(User.id == user_id, User.deleted == False).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """Get a user by username."""
    return db.query(User).filter(User.username == username, User.deleted == False).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get a user by email."""
    return db.query(User).filter(User.email == email, User.deleted == False).first()


def get_users(db: Session, skip: int = 0, limit: int = 100) -> List[User]:
    """Get a list of users."""
    return db.query(User).filter(User.deleted == False).offset(skip).limit(limit).all()


def get_admin_users(db: Session) -> List[User]:
    """Get all admin users."""
    return db.query(User).filter(User.is_admin == True, User.deleted == False).all()


def authenticate_admin(db: Session, username: str, password: str) -> Optional[User]:
    """Authenticate an admin user."""
    admin_creds = db.query(AdminCredentials).join(User).filter(User.username == username).first()
    if not admin_creds:
        return None
    
    if not verify_password(password, admin_creds.password_hash):
        return None
    
    admin = db.query(User).filter(User.id == admin_creds.user_id).first()
    if admin:
        admin.last_login = datetime.now(UTC)
        db.commit()
    
    return admin


def update_user_last_login(db: Session, user_id: str) -> None:
    """Update the last login time for a user."""
    user = get_user(db, user_id)
    if user:
        user.last_login = datetime.now(UTC)
        db.commit()


def authenticate_player(db: Session, username: str, password: str) -> Optional[User]:
    """Authenticate a non-admin player."""
    player_creds = db.query(PlayerCredentials).join(User).filter(User.username == username).first()
    if not player_creds:
        return None
    
    if not verify_password(password, player_creds.password_hash):
        return None
    
    player = db.query(User).filter(User.id == player_creds.user_id).first()
    if player:
        player.last_login = datetime.now(UTC)
        db.commit()
    
    return player