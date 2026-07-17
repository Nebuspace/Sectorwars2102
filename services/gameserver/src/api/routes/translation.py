"""
Translation API routes for internationalization support
"""

import logging
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.database import get_async_session
from src.services.translation_service import TranslationService, get_translation_service
from src.auth.admin_scopes import GALAXY_MANAGE, PLAYERS_VIEW
from src.auth.dependencies import get_current_user, require_scope
from src.models.user import User
from src.services.admin_action_log_service import log_admin_action

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/i18n", tags=["Internationalization"])


# Pydantic models for request/response
class LanguageResponse(BaseModel):
    """Language information response"""
    code: str
    name: str
    nativeName: str
    direction: str
    isActive: bool
    completionPercentage: int


class TranslationRequest(BaseModel):
    """Translation update request"""
    key: str = Field(..., description="Translation key in dot notation")
    value: str = Field(..., description="Translated text")
    context: Optional[str] = Field(None, description="Context for translators")


class BulkTranslationRequest(BaseModel):
    """Bulk translation import request"""
    translations: Dict[str, Any] = Field(..., description="Nested translation dictionary")
    overwrite: bool = Field(False, description="Whether to overwrite existing translations")


class LanguagePreferenceRequest(BaseModel):
    """User language preference request"""
    languageCode: str = Field(..., description="Language code to set as preference")


class TranslationProgressResponse(BaseModel):
    """Translation progress response"""
    language: str
    overallCompletion: float
    totalKeys: int
    translatedKeys: int
    namespaces: Dict[str, Any]


# Public endpoints (no authentication required)

@router.get("/languages", response_model=List[LanguageResponse])
async def get_supported_languages(
    active_only: bool = True,
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Get list of supported languages"""
    try:
        languages = await translation_service.get_supported_languages(active_only)
        return languages
    except Exception as e:
        logger.error(f"Failed to get languages: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve languages")


@router.get("/detect")
async def detect_language(
    request: Request,
    accept_language: Optional[str] = Header(None, alias="Accept-Language"),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Detect user's preferred language from browser headers"""
    try:
        detected_language = await translation_service.detect_user_language(accept_language or "")
        return {"detectedLanguage": detected_language}
    except Exception as e:
        logger.error(f"Failed to detect language: {e}")
        return {"detectedLanguage": "en"}


@router.get("/{language_code}")
async def get_translations(
    language_code: str,
    namespace: Optional[str] = None,
    include_context: bool = False,
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Get translations for a language and optional namespace"""
    try:
        translations = await translation_service.get_translations(
            language_code, namespace, include_context
        )
        return translations
    except Exception as e:
        logger.error(f"Failed to get translations: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve translations")


@router.get("/{language_code}/{namespace}")
async def get_namespace_translations(
    language_code: str,
    namespace: str,
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Get translations for a specific namespace"""
    try:
        translations = await translation_service.get_namespace_translations(
            language_code, namespace
        )
        return translations
    except Exception as e:
        logger.error(f"Failed to get namespace translations: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve translations")


# User-authenticated endpoints

@router.get("/user/preference")
async def get_user_language_preference(
    current_user: User = Depends(get_current_user),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Get current user's language preference"""
    try:
        language_code = await translation_service.get_user_language_preference(current_user.id)
        return {"languageCode": language_code}
    except Exception as e:
        logger.error(f"Failed to get user language preference: {e}")
        raise HTTPException(status_code=500, detail="Failed to get language preference")


@router.post("/user/preference")
async def set_user_language_preference(
    request: LanguagePreferenceRequest,
    current_user: User = Depends(get_current_user),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Set current user's language preference"""
    try:
        success = await translation_service.set_user_language_preference(
            current_user.id, request.languageCode, manual_override=True
        )
        
        if success:
            return {"success": True, "languageCode": request.languageCode}
        else:
            raise HTTPException(status_code=400, detail="Failed to set language preference")
            
    except Exception as e:
        logger.error(f"Failed to set user language preference: {e}")
        raise HTTPException(status_code=500, detail="Failed to set language preference")


@router.get("/user/ai-context")
async def get_ai_language_context(
    current_user: User = Depends(get_current_user),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Get AI language context for current user"""
    try:
        user_language = await translation_service.get_user_language_preference(current_user.id)
        context = await translation_service.get_ai_language_context(user_language)
        return {
            "userLanguage": user_language,
            "aiContext": context
        }
    except Exception as e:
        logger.error(f"Failed to get AI language context: {e}")
        raise HTTPException(status_code=500, detail="Failed to get AI context")


# Admin-only endpoints

@router.get("/admin/progress/{language_code}", response_model=TranslationProgressResponse)
async def get_translation_progress(
    language_code: str,
    admin_user: User = Depends(require_scope(PLAYERS_VIEW)),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Get translation progress for a language (admin only)"""
    try:
        progress = await translation_service.get_translation_progress(language_code)
        return progress
    except Exception as e:
        logger.error(f"Failed to get translation progress: {e}")
        raise HTTPException(status_code=500, detail="Failed to get translation progress")


@router.post("/admin/translation/{language_code}/{namespace}")
async def set_translation(
    language_code: str,
    namespace: str,
    request: TranslationRequest,
    admin_user: User = Depends(require_scope(GALAXY_MANAGE)),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Set or update a translation (admin only)"""
    try:

        log_admin_action(
            translation_service.db,
            actor=admin_user,
            scope_used=GALAXY_MANAGE,
            action="translation_set",
            target_type="translation",
            target_id=request.key,
            payload={"key": request.key, "namespace": namespace, "language": language_code},
        )
        success = await translation_service.set_translation(
            key=request.key,
            language_code=language_code,
            namespace=namespace,
            value=request.value,
            context=request.context,
            changed_by=admin_user.id
        )
        
        if success:
            return {"success": True, "key": request.key, "language": language_code}
        else:
            raise HTTPException(status_code=400, detail="Failed to set translation")
            
    except Exception as e:
        logger.error(f"Failed to set translation: {e}")
        raise HTTPException(status_code=500, detail="Failed to set translation")


@router.post("/admin/bulk/{language_code}/{namespace}")
async def bulk_import_translations(
    language_code: str,
    namespace: str,
    request: BulkTranslationRequest,
    admin_user: User = Depends(require_scope(GALAXY_MANAGE)),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Bulk import translations (admin only)"""
    try:

        log_admin_action(
            translation_service.db,
            actor=admin_user,
            scope_used=GALAXY_MANAGE,
            action="translation_bulk_import",
            target_type="translation",
            target_id=f"{language_code}/{namespace}",
            payload={"namespace": namespace, "language": language_code, "overwrite": request.overwrite},
        )
        result = await translation_service.bulk_import_translations(
            translations=request.translations,
            language_code=language_code,
            namespace=namespace,
            overwrite=request.overwrite
        )
        return result
    except Exception as e:
        logger.error(f"Failed to bulk import translations: {e}")
        raise HTTPException(status_code=500, detail="Failed to import translations")


@router.post("/admin/initialize")
async def initialize_translation_data(
    admin_user: User = Depends(require_scope(GALAXY_MANAGE)),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Initialize default translation data (admin only)"""
    try:

        log_admin_action(
            translation_service.db,
            actor=admin_user,
            scope_used=GALAXY_MANAGE,
            action="translation_initialize",
            target_type="translation",
            target_id="defaults",
            payload={},
        )
        success = await translation_service.initialize_default_data()
        if success:
            return {"success": True, "message": "Translation data initialized"}
        else:
            raise HTTPException(status_code=500, detail="Failed to initialize translation data")
    except Exception as e:
        logger.error(f"Failed to initialize translation data: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize translation data")


@router.get("/admin/languages/all")
async def get_all_languages(
    admin_user: User = Depends(require_scope(PLAYERS_VIEW)),
    translation_service: TranslationService = Depends(get_translation_service)
):
    """Get all languages including inactive ones (admin only)"""
    try:
        languages = await translation_service.get_supported_languages(active_only=False)
        return languages
    except Exception as e:
        logger.error(f"Failed to get all languages: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve languages")


# Health check endpoint
@router.get("/health")
async def translation_health_check():
    """Health check for translation service"""
    return {
        "status": "healthy",
        "service": "translation",
        "version": "1.0.0"
    }