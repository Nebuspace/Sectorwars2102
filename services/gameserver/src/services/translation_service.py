"""
Translation service for internationalization support
Provides unified translation management for all applications
"""

import logging
import json
import re
import html
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from fastapi import HTTPException, Depends
from datetime import datetime
import traceback

from src.models.translation import (
    Language, TranslationNamespace, TranslationKey,
    UserLanguagePreference, TranslationProgress, TranslationAuditLog,
    DEFAULT_LANGUAGES, DEFAULT_NAMESPACES
)
from src.models.user import User
from src.core.database import get_db

logger = logging.getLogger(__name__)


class TranslationService:
    """Unified translation service for all applications"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def _handle_error(self, operation: str, error: Exception, user_facing: bool = True) -> None:
        """Standardized error handling for translation service operations"""
        error_id = f"TRANS_{hash(str(error)) % 10000:04d}"
        
        # Log detailed error for debugging
        logger.error(
            f"Translation Service Error [{error_id}]: {operation} failed",
            extra={
                "operation": operation,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "traceback": traceback.format_exc(),
                "error_id": error_id
            }
        )
        
        # Rollback database transaction if active
        try:
            if self.db.in_transaction():
                self.db.rollback()
        except Exception as rollback_error:
            logger.error(f"Failed to rollback transaction: {rollback_error}")
        
        # Raise appropriate user-facing error
        if user_facing:
            if isinstance(error, HTTPException):
                raise error
            elif "validation" in str(error).lower() or "invalid" in str(error).lower():
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid input for {operation}. Error ID: {error_id}"
                )
            elif "not found" in str(error).lower() or "does not exist" in str(error).lower():
                raise HTTPException(
                    status_code=404, 
                    detail=f"Resource not found for {operation}. Error ID: {error_id}"
                )
            elif "permission" in str(error).lower() or "unauthorized" in str(error).lower():
                raise HTTPException(
                    status_code=403, 
                    detail=f"Access denied for {operation}. Error ID: {error_id}"
                )
            else:
                raise HTTPException(
                    status_code=500, 
                    detail=f"Internal error during {operation}. Error ID: {error_id}"
                )
    
    def validate_translation_key(self, key: str) -> bool:
        """Validate translation key format and security"""
        if not key or not isinstance(key, str):
            return False
        
        # Length check (1-200 characters)
        if len(key) < 1 or len(key) > 200:
            return False
        
        # Format validation: alphanumeric, dots, underscores, hyphens only
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9._-]*$', key):
            return False
        
        # Security checks - block suspicious patterns
        suspicious_patterns = [
            '../', '..\\', '/etc/', '\\etc\\',
            'drop', 'select', 'union', 'insert', 'delete',
            'script', 'javascript:', 'vbscript:', 'onload', 'onerror'
        ]
        
        key_lower = key.lower()
        for pattern in suspicious_patterns:
            if pattern in key_lower:
                return False
        
        return True
    
    def validate_translation_value(self, value: str) -> Tuple[bool, str]:
        """Validate translation value and sanitize if needed"""
        if not isinstance(value, str):
            return False, "Value must be a string"
        
        # Size limit check (10KB)
        if len(value) > 10000:
            return False, f"Value too large: {len(value)} characters (max 10000)"
        
        # Check for dangerous HTML/script content
        dangerous_patterns = [
            r'<script[^>]*>',
            r'<iframe[^>]*>',
            r'javascript:',
            r'vbscript:',
            r'onload\s*=',
            r'onerror\s*=',
            r'onclick\s*=',
            r'onmouseover\s*='
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, value, re.IGNORECASE):
                return False, f"Dangerous content detected: script or event handlers not allowed"
        
        # Sanitize HTML entities but preserve intended formatting
        # Only escape if it looks like unintended HTML
        if '<' in value and '>' in value and not self._is_intentional_formatting(value):
            sanitized_value = html.escape(value)
            logger.warning(f"HTML content sanitized in translation value: {value[:50]}...")
            return True, sanitized_value
        
        return True, value
    
    def _is_intentional_formatting(self, value: str) -> bool:
        """Check if HTML tags are intentional formatting (like <br>, <b>, etc.)"""
        allowed_tags = ['<br>', '<br/>', '<b>', '</b>', '<i>', '</i>', '<em>', '</em>', '<strong>', '</strong>']
        
        # Find all HTML-like patterns. `[^<>]+` (not `[^>]+`) prevents O(n²)
        # backtracking on inputs with many leading `<` characters
        # (py/polynomial-redos).
        html_patterns = re.findall(r'<[^<>]+>', value, re.IGNORECASE)
        
        # Check if all found tags are in allowed list
        for pattern in html_patterns:
            if pattern.lower() not in [tag.lower() for tag in allowed_tags]:
                return False
        
        return True
    
    def validate_language_code(self, language_code: str) -> bool:
        """Validate language code format"""
        if not language_code or not isinstance(language_code, str):
            return False
        
        # Length check (2 characters only for base language codes)
        if len(language_code) != 2:
            return False
        
        # Format check: exactly 2 lowercase letters, no regional codes
        if not re.match(r'^[a-z]{2}$', language_code):
            return False
        
        return True
    
    def validate_namespace_name(self, namespace: str) -> bool:
        """Validate namespace name format"""
        if not namespace or not isinstance(namespace, str):
            return False
        
        # Length check (1-50 characters)
        if len(namespace) < 1 or len(namespace) > 50:
            return False
        
        # Format validation: alphanumeric, underscores, hyphens only
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', namespace):
            return False
        
        return True
    
    async def initialize_default_data(self) -> bool:
        """Initialize default languages and namespaces"""
        try:
            # Check if data already exists
            existing_languages = self.db.query(Language).count()
            if existing_languages > 0:
                logger.info("Translation data already initialized")
                return True
            
            # Create default languages
            for code, name, native_name, direction, is_active, completion in DEFAULT_LANGUAGES:
                language = Language(
                    code=code,
                    name=name,
                    native_name=native_name,
                    direction=direction,
                    is_active=is_active,
                    completion_percentage=completion
                )
                self.db.add(language)
            
            # Create default namespaces
            for name, description, application in DEFAULT_NAMESPACES:
                namespace = TranslationNamespace(
                    name=name,
                    description=description,
                    application=application,
                    is_active=True
                )
                self.db.add(namespace)
            
            self.db.commit()
            logger.info("Translation system initialized with default data")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize translation data: {e}")
            self.db.rollback()
            return False
    
    async def get_supported_languages(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get list of supported languages"""
        try:
            query = self.db.query(Language)
            if active_only:
                query = query.filter(Language.is_active == True)
            
            languages = query.order_by(Language.completion_percentage.desc()).all()
            
            return [
                {
                    "code": lang.code,
                    "name": lang.name,
                    "nativeName": lang.native_name,
                    "direction": lang.direction,
                    "isActive": lang.is_active,
                    "completionPercentage": lang.completion_percentage
                }
                for lang in languages
            ]
        except Exception as e:
            logger.error(f"Failed to get supported languages: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve languages")
    
    async def get_user_language_preference(self, user_id: int) -> Optional[str]:
        """Get user's preferred language"""
        try:
            preference = (
                self.db.query(UserLanguagePreference)
                .join(Language)
                .filter(UserLanguagePreference.user_id == user_id)
                .first()
            )
            
            if preference:
                return preference.language.code
            
            # Default to English if no preference set
            return "en"
            
        except Exception as e:
            logger.error(f"Failed to get user language preference: {e}")
            return "en"
    
    async def set_user_language_preference(
        self, 
        user_id: int, 
        language_code: str, 
        manual_override: bool = True
    ) -> bool:
        """Set user's language preference"""
        try:
            # Validate language code format first
            if not self.validate_language_code(language_code):
                raise HTTPException(status_code=400, detail=f"Invalid language code format: '{language_code}'")
            
            # Validate user ID
            if not isinstance(user_id, int) or user_id <= 0:
                raise HTTPException(status_code=400, detail="Invalid user ID")
            
            # Validate language exists and is active
            language = (
                self.db.query(Language)
                .filter(and_(Language.code == language_code, Language.is_active == True))
                .first()
            )
            
            if not language:
                raise HTTPException(status_code=400, detail=f"Language '{language_code}' not supported")
            
            # Check if preference already exists
            existing_pref = (
                self.db.query(UserLanguagePreference)
                .filter(UserLanguagePreference.user_id == user_id)
                .first()
            )
            
            if existing_pref:
                # Update existing preference
                existing_pref.language_id = language.id
                existing_pref.manual_override = manual_override
                existing_pref.updated_at = datetime.utcnow()
            else:
                # Create new preference
                new_pref = UserLanguagePreference(
                    user_id=user_id,
                    language_id=language.id,
                    manual_override=manual_override
                )
                self.db.add(new_pref)
            
            self.db.commit()
            logger.info(f"Set language preference for user {user_id}: {language_code}")
            return True
            
        except Exception as e:
            self._handle_error("set_user_language_preference", e)
            return False
    
    async def get_translations(
        self, 
        language_code: str, 
        namespace: Optional[str] = None,
        include_context: bool = False
    ) -> Dict[str, Any]:
        """Get translations for a language and optional namespace"""
        try:
            # Validate inputs
            if not self.validate_language_code(language_code):
                raise HTTPException(status_code=400, detail=f"Invalid language code format: '{language_code}'")
            
            if namespace and not self.validate_namespace_name(namespace):
                raise HTTPException(status_code=400, detail=f"Invalid namespace format: '{namespace}'")
            
            # Validate language exists
            language = (
                self.db.query(Language)
                .filter(Language.code == language_code)
                .first()
            )
            
            if not language:
                # Fallback to English if language not found
                language = self.db.query(Language).filter(Language.code == "en").first()
                if not language:
                    raise HTTPException(status_code=500, detail="Base language 'en' not configured")
            
            # Build query
            query = (
                self.db.query(TranslationKey)
                .join(TranslationNamespace)
                .filter(TranslationKey.language_id == language.id)
            )
            
            if namespace:
                query = query.filter(TranslationNamespace.name == namespace)
            
            translations = query.all()
            
            # Organize translations by namespace and key
            result = {}
            for trans in translations:
                ns_name = trans.namespace.name
                
                if ns_name not in result:
                    result[ns_name] = {}
                
                # Create nested key structure (e.g., "buttons.save" -> {"buttons": {"save": "Save"}})
                self._set_nested_value(result[ns_name], trans.key, {
                    "value": trans.value,
                    "context": trans.context if include_context else None,
                    "verified": trans.is_verified
                } if include_context else trans.value)
            
            return result
            
        except Exception as e:
            self._handle_error("get_translations", e)
    
    async def get_namespace_translations(
        self, 
        language_code: str, 
        namespace: str
    ) -> Dict[str, str]:
        """Get translations for a specific namespace (simplified format)"""
        try:
            # Validate inputs
            if not self.validate_language_code(language_code):
                raise HTTPException(status_code=400, detail=f"Invalid language code format: '{language_code}'")
            
            if not self.validate_namespace_name(namespace):
                raise HTTPException(status_code=400, detail=f"Invalid namespace format: '{namespace}'")
            
            result = await self.get_translations(language_code, namespace)
            return result.get(namespace, {})
        except Exception as e:
            self._handle_error("get_namespace_translations", e, user_facing=False)
            return {}
    
    async def set_translation(
        self,
        key: str,
        language_code: str,
        namespace: str,
        value: str,
        context: Optional[str] = None,
        changed_by: Optional[int] = None
    ) -> bool:
        """Set or update a translation"""
        try:
            # Validate all inputs
            if not self.validate_translation_key(key):
                raise HTTPException(status_code=400, detail=f"Invalid translation key format: '{key}'")
            
            if not self.validate_language_code(language_code):
                raise HTTPException(status_code=400, detail=f"Invalid language code format: '{language_code}'")
            
            if not self.validate_namespace_name(namespace):
                raise HTTPException(status_code=400, detail=f"Invalid namespace format: '{namespace}'")
            
            # Validate and sanitize translation value
            is_valid_value, sanitized_value = self.validate_translation_value(value)
            if not is_valid_value:
                raise HTTPException(status_code=400, detail=f"Invalid translation value: {sanitized_value}")
            
            # Validate context if provided
            if context:
                is_valid_context, sanitized_context = self.validate_translation_value(context)
                if not is_valid_context:
                    raise HTTPException(status_code=400, detail=f"Invalid context value: {sanitized_context}")
                context = sanitized_context
            
            # Validate changed_by if provided
            if changed_by is not None and (not isinstance(changed_by, int) or changed_by <= 0):
                raise HTTPException(status_code=400, detail="Invalid user ID for changed_by")
            
            # Get language and namespace
            language = self.db.query(Language).filter(Language.code == language_code).first()
            if not language:
                raise HTTPException(status_code=400, detail=f"Language '{language_code}' not found")
            
            ns = self.db.query(TranslationNamespace).filter(TranslationNamespace.name == namespace).first()
            if not ns:
                raise HTTPException(status_code=400, detail=f"Namespace '{namespace}' not found")
            
            # Check if translation exists
            existing = (
                self.db.query(TranslationKey)
                .filter(
                    and_(
                        TranslationKey.key == key,
                        TranslationKey.language_id == language.id,
                        TranslationKey.namespace_id == ns.id
                    )
                )
                .first()
            )
            
            if existing:
                # Create audit log for the change
                if existing.value != sanitized_value:
                    audit_log = TranslationAuditLog(
                        translation_key_id=existing.id,
                        old_value=existing.value,
                        new_value=sanitized_value,
                        changed_by=changed_by,
                        change_reason="Manual update"
                    )
                    self.db.add(audit_log)
                
                # Update existing translation with sanitized value
                existing.value = sanitized_value
                if context:
                    existing.context = context
                existing.updated_at = datetime.utcnow()
            else:
                # Create new translation with sanitized value
                new_translation = TranslationKey(
                    key=key,
                    language_id=language.id,
                    namespace_id=ns.id,
                    value=sanitized_value,
                    context=context
                )
                self.db.add(new_translation)
            
            self.db.commit()
            
            # Update progress tracking
            await self._update_translation_progress(language.id, ns.id)
            
            return True
            
        except Exception as e:
            self._handle_error("set_translation", e)
            return False
    
    async def bulk_import_translations(
        self,
        translations: Dict[str, Dict[str, str]],
        language_code: str,
        namespace: str,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """Bulk import translations from a dictionary"""
        try:
            # Validate inputs first
            if not self.validate_language_code(language_code):
                raise HTTPException(status_code=400, detail=f"Invalid language code format: '{language_code}'")
            
            if not self.validate_namespace_name(namespace):
                raise HTTPException(status_code=400, detail=f"Invalid namespace format: '{namespace}'")
            
            if not isinstance(translations, dict) or not translations:
                raise HTTPException(status_code=400, detail="Translations must be a non-empty dictionary")
            
            imported_count = 0
            updated_count = 0
            skipped_count = 0
            error_count = 0
            
            # Flatten nested translation dictionary
            flat_translations = self._flatten_translations(translations)
            
            for key, value in flat_translations.items():
                try:
                    # Validate each key-value pair
                    if not self.validate_translation_key(key):
                        logger.warning(f"Skipping invalid translation key: {key}")
                        error_count += 1
                        continue
                    
                    is_valid_value, _ = self.validate_translation_value(value)
                    if not is_valid_value:
                        logger.warning(f"Skipping invalid translation value for key: {key}")
                        error_count += 1
                        continue
                    
                    existing = await self.get_translation_key(key, language_code, namespace)
                    
                    if existing and not overwrite:
                        skipped_count += 1
                        continue
                    
                    success = await self.set_translation(key, language_code, namespace, value)
                    
                    if success:
                        if existing:
                            updated_count += 1
                        else:
                            imported_count += 1
                    else:
                        error_count += 1
                        
                except Exception as e:
                    logger.error(f"Error processing translation key '{key}': {e}")
                    error_count += 1
            
            return {
                "imported": imported_count,
                "updated": updated_count,
                "skipped": skipped_count,
                "errors": error_count,
                "total": len(flat_translations)
            }
            
        except Exception as e:
            logger.error(f"Failed to bulk import translations: {e}")
            raise HTTPException(status_code=500, detail="Bulk import failed")
    
    async def get_translation_progress(self, language_code: str) -> Dict[str, Any]:
        """Get translation progress for a language"""
        try:
            # Validate input
            if not self.validate_language_code(language_code):
                raise HTTPException(status_code=400, detail=f"Invalid language code format: '{language_code}'")
            
            language = self.db.query(Language).filter(Language.code == language_code).first()
            if not language:
                raise HTTPException(status_code=404, detail="Language not found")
            
            progress_data = (
                self.db.query(TranslationProgress)
                .join(TranslationNamespace)
                .filter(TranslationProgress.language_id == language.id)
                .all()
            )
            
            namespaces = {}
            total_keys = 0
            total_translated = 0
            
            for progress in progress_data:
                namespaces[progress.namespace.name] = {
                    "totalKeys": progress.total_keys,
                    "translatedKeys": progress.translated_keys,
                    "verifiedKeys": progress.verified_keys,
                    "completionPercentage": progress.completion_percentage,
                    "lastUpdated": progress.last_updated.isoformat()
                }
                total_keys += progress.total_keys
                total_translated += progress.translated_keys
            
            overall_percentage = (total_translated / total_keys * 100) if total_keys > 0 else 0
            
            return {
                "language": language_code,
                "overallCompletion": round(overall_percentage, 2),
                "totalKeys": total_keys,
                "translatedKeys": total_translated,
                "namespaces": namespaces
            }
            
        except Exception as e:
            logger.error(f"Failed to get translation progress: {e}")
            raise HTTPException(status_code=500, detail="Failed to get progress")
    
    async def get_ai_language_context(self, user_language: str) -> str:
        """Get language context for AI responses"""
        language_contexts = {
            'en': 'Respond in English with professional space trading terminology',
            'es': 'Responde en español con terminología profesional de comercio espacial',
            'zh': '用中文回复，使用专业的太空贸易术语',
            'fr': 'Répondez en français avec une terminologie commerciale spatiale professionnelle',
            'pt': 'Responda em português com terminologia profissional de comércio espacial',
            'de': 'Antworten Sie auf Deutsch mit professioneller Weltraumhandels-Terminologie',
            'ja': '宇宙貿易の専門用語を使って日本語で回答してください',
            'ru': 'Отвечайте на русском языке, используя профессиональную терминологию космической торговли',
            'ar': 'أجب باللغة العربية باستخدام مصطلحات التجارة الفضائية المهنية',
            'ko': '전문적인 우주 무역 용어를 사용하여 한국어로 응답하세요',
            'it': 'Rispondi in italiano con terminologia professionale del commercio spaziale',
            'nl': 'Antwoord in het Nederlands met professionele ruimtehandel terminologie'
        }
        
        return language_contexts.get(user_language, language_contexts['en'])
    
    async def detect_user_language(self, accept_language_header: str) -> str:
        """Detect user's preferred language from browser headers"""
        try:
            # Validate input
            if not isinstance(accept_language_header, str):
                logger.warning(f"Invalid Accept-Language header type: {type(accept_language_header)}")
                return "en"
            
            # Basic sanitization - remove potentially dangerous characters
            if any(char in accept_language_header for char in ['<', '>', '&', '"', "'", '\n', '\r']):
                logger.warning(f"Suspicious characters in Accept-Language header: {accept_language_header[:50]}")
                return "en"
            
            # Parse Accept-Language header
            languages = []
            if accept_language_header:
                for lang_item in accept_language_header.split(','):
                    parts = lang_item.strip().split(';')
                    lang_code = parts[0].strip()
                    
                    # Extract quality value (q parameter)
                    quality = 1.0
                    if len(parts) > 1 and parts[1].startswith('q='):
                        try:
                            quality = float(parts[1][2:])
                        except ValueError:
                            quality = 1.0
                    
                    languages.append((lang_code, quality))
            
            # Sort by quality
            languages.sort(key=lambda x: x[1], reverse=True)
            
            # Check if any preferred language is supported
            supported_languages = await self.get_supported_languages(active_only=True)
            supported_codes = {lang['code'] for lang in supported_languages}
            
            for lang_code, _ in languages:
                # Handle variants (e.g., en-US -> en)
                base_code = lang_code.split('-')[0]
                if lang_code in supported_codes:
                    return lang_code
                elif base_code in supported_codes:
                    return base_code
            
            # Default to English
            return "en"
            
        except Exception as e:
            logger.error(f"Failed to detect language: {e}")
            return "en"
    
    # Helper methods
    
    def _set_nested_value(self, dictionary: Dict, key: str, value: Any):
        """Set nested dictionary value using dot notation key"""
        keys = key.split('.')
        current = dictionary
        
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        
        current[keys[-1]] = value
    
    def _flatten_translations(self, nested_dict: Dict, prefix: str = "") -> Dict[str, str]:
        """Flatten nested dictionary to dot notation keys"""
        flattened = {}
        
        for key, value in nested_dict.items():
            new_key = f"{prefix}.{key}" if prefix else key
            
            if isinstance(value, dict):
                flattened.update(self._flatten_translations(value, new_key))
            else:
                flattened[new_key] = str(value)
        
        return flattened
    
    async def get_translation_key(
        self, 
        key: str, 
        language_code: str, 
        namespace: str
    ) -> Optional[TranslationKey]:
        """Get a specific translation key"""
        try:
            return (
                self.db.query(TranslationKey)
                .join(Language)
                .join(TranslationNamespace)
                .filter(
                    and_(
                        TranslationKey.key == key,
                        Language.code == language_code,
                        TranslationNamespace.name == namespace
                    )
                )
                .first()
            )
        except Exception as e:
            logger.error(f"Failed to get translation key: {e}")
            return None
    
    async def _update_translation_progress(self, language_id: int, namespace_id: int):
        """Update translation progress for a language/namespace combination"""
        try:
            # Count total keys in namespace (from English base)
            english_lang = self.db.query(Language).filter(Language.code == "en").first()
            if not english_lang:
                return
            
            total_keys = (
                self.db.query(TranslationKey)
                .filter(
                    and_(
                        TranslationKey.language_id == english_lang.id,
                        TranslationKey.namespace_id == namespace_id
                    )
                )
                .count()
            )
            
            # Count translated keys
            translated_keys = (
                self.db.query(TranslationKey)
                .filter(
                    and_(
                        TranslationKey.language_id == language_id,
                        TranslationKey.namespace_id == namespace_id
                    )
                )
                .count()
            )
            
            # Count verified keys
            verified_keys = (
                self.db.query(TranslationKey)
                .filter(
                    and_(
                        TranslationKey.language_id == language_id,
                        TranslationKey.namespace_id == namespace_id,
                        TranslationKey.is_verified == True
                    )
                )
                .count()
            )
            
            # Calculate completion percentage
            completion = (translated_keys / total_keys * 100) if total_keys > 0 else 0
            
            # Update or create progress record
            progress = (
                self.db.query(TranslationProgress)
                .filter(
                    and_(
                        TranslationProgress.language_id == language_id,
                        TranslationProgress.namespace_id == namespace_id
                    )
                )
                .first()
            )
            
            if progress:
                progress.total_keys = total_keys
                progress.translated_keys = translated_keys
                progress.verified_keys = verified_keys
                progress.completion_percentage = round(completion, 2)
                progress.last_updated = datetime.utcnow()
            else:
                progress = TranslationProgress(
                    language_id=language_id,
                    namespace_id=namespace_id,
                    total_keys=total_keys,
                    translated_keys=translated_keys,
                    verified_keys=verified_keys,
                    completion_percentage=round(completion, 2)
                )
                self.db.add(progress)
            
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Failed to update translation progress: {e}")
            self.db.rollback()


# Service factory function
def get_translation_service(db: Session = Depends(get_db)) -> TranslationService:
    """Get translation service instance"""
    return TranslationService(db)