"""
Translation system database models for internationalization support
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from src.core.database import Base


class NamespaceType(enum.Enum):
    """Translation namespace types for organization"""
    COMMON = "common"          # Shared across applications
    ADMIN = "admin"           # Admin UI specific
    GAME = "game"             # Player Client specific
    AUTH = "auth"             # Authentication flows
    AI = "ai"                 # AI assistant content
    MARKETING = "marketing"    # Landing page content
    ERRORS = "errors"         # Error messages
    VALIDATION = "validation"  # Form validation messages


class Language(Base):
    """Supported languages configuration"""
    __tablename__ = "languages"
    
    id = Column(Integer, primary_key=True)
    code = Column(String(10), unique=True, nullable=False, index=True)  # ISO language code
    name = Column(String(100), nullable=False)                         # Language name in English
    native_name = Column(String(100), nullable=False)                  # Language name in native script
    direction = Column(String(3), default="ltr", nullable=False)       # Text direction: ltr/rtl
    is_active = Column(Boolean, default=True, nullable=False)          # Language is available
    completion_percentage = Column(Integer, default=0)                 # Translation completion %
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    translation_keys = relationship("TranslationKey", back_populates="language")
    user_preferences = relationship("UserLanguagePreference", back_populates="language")
    
    def __repr__(self):
        return f"<Language(code='{self.code}', name='{self.name}')>"


class TranslationNamespace(Base):
    """Translation namespaces for organizing content"""
    __tablename__ = "translation_namespaces"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False, index=True)  # Namespace identifier
    description = Column(Text)                                          # Namespace description
    application = Column(String(50))                                    # Target application
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    translation_keys = relationship("TranslationKey", back_populates="namespace")
    
    def __repr__(self):
        return f"<TranslationNamespace(name='{self.name}', app='{self.application}')>"


class TranslationKey(Base):
    """Translation keys and their values in different languages"""
    __tablename__ = "translation_keys"
    
    id = Column(Integer, primary_key=True)
    key = Column(String(200), nullable=False, index=True)               # Translation key (dot notation)
    language_id = Column(Integer, ForeignKey("languages.id"), nullable=False, index=True)
    namespace_id = Column(Integer, ForeignKey("translation_namespaces.id"), nullable=False, index=True)
    value = Column(Text, nullable=False)                               # Translated text
    context = Column(Text)                                             # Context for translators
    pluralization_rule = Column(String(50))                           # Pluralization pattern
    interpolation_vars = Column(Text)                                  # Expected variables JSON
    is_verified = Column(Boolean, default=False, nullable=False)       # Translation verified
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Foreign key relationships
    language = relationship("Language", back_populates="translation_keys")
    namespace = relationship("TranslationNamespace", back_populates="translation_keys")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_translation_lookup', 'key', 'language_id', 'namespace_id'),
        Index('idx_namespace_language', 'namespace_id', 'language_id'),
    )
    
    def __repr__(self):
        return f"<TranslationKey(key='{self.key}', lang='{self.language.code}', ns='{self.namespace.name}')>"


class UserLanguagePreference(Base):
    """User language preferences"""
    __tablename__ = "user_language_preferences"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    language_id = Column(Integer, ForeignKey("languages.id"), nullable=False, index=True)
    detected_language = Column(String(10))                             # Browser-detected language
    manual_override = Column(Boolean, default=False, nullable=False)    # User manually changed
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    language = relationship("Language", back_populates="user_preferences")
    
    def __repr__(self):
        return f"<UserLanguagePreference(user_id={self.user_id}, lang='{self.language.code}')>"


class TranslationAuditLog(Base):
    """Audit log for translation changes"""
    __tablename__ = "translation_audit_logs"
    
    id = Column(Integer, primary_key=True)
    translation_key_id = Column(Integer, ForeignKey("translation_keys.id"), nullable=False, index=True)
    old_value = Column(Text)                                           # Previous translation
    new_value = Column(Text, nullable=False)                          # New translation
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))   # User who made change
    change_reason = Column(String(200))                               # Reason for change
    is_automated = Column(Boolean, default=False, nullable=False)      # Automated vs manual change
    created_at = Column(DateTime, server_default=func.now())
    
    def __repr__(self):
        return f"<TranslationAuditLog(key_id={self.translation_key_id}, changed_at='{self.created_at}')>"


class TranslationProgress(Base):
    """Track translation progress by namespace and language"""
    __tablename__ = "translation_progress"
    
    id = Column(Integer, primary_key=True)
    language_id = Column(Integer, ForeignKey("languages.id"), nullable=False, index=True)
    namespace_id = Column(Integer, ForeignKey("translation_namespaces.id"), nullable=False, index=True)
    total_keys = Column(Integer, default=0, nullable=False)
    translated_keys = Column(Integer, default=0, nullable=False)
    verified_keys = Column(Integer, default=0, nullable=False)
    completion_percentage = Column(Integer, default=0, nullable=False)
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    language = relationship("Language")
    namespace = relationship("TranslationNamespace")
    
    # Unique constraint to prevent duplicates
    __table_args__ = (
        Index('idx_progress_unique', 'language_id', 'namespace_id', unique=True),
    )
    
    def __repr__(self):
        return f"<TranslationProgress(lang='{self.language.code}', ns='{self.namespace.name}', {self.completion_percentage}%)>"


# Default language and namespace data for seeding
DEFAULT_LANGUAGES = [
    ("en", "English", "English", "ltr", True, 100),
    ("es", "Spanish", "Español", "ltr", True, 0),
    ("zh", "Chinese (Simplified)", "中文(简体)", "ltr", True, 0),
    ("fr", "French", "Français", "ltr", True, 0),
    ("pt", "Portuguese", "Português", "ltr", True, 0),
    ("de", "German", "Deutsch", "ltr", False, 0),
    ("ja", "Japanese", "日本語", "ltr", False, 0),
    ("ru", "Russian", "Русский", "ltr", False, 0),
    ("ar", "Arabic", "العربية", "rtl", False, 0),
    ("ko", "Korean", "한국어", "ltr", False, 0),
    ("it", "Italian", "Italiano", "ltr", False, 0),
    ("nl", "Dutch", "Nederlands", "ltr", False, 0),
]

DEFAULT_NAMESPACES = [
    ("common", "Shared content across applications", "shared"),
    ("admin", "Admin UI specific content", "admin-ui"),
    ("game", "Player Client specific content", "player-client"),
    ("auth", "Authentication flows", "shared"),
    ("ai", "AI assistant content", "shared"),
    ("marketing", "Landing page content", "player-client"),
    ("errors", "Error messages", "shared"),
    ("validation", "Form validation messages", "shared"),
]