"""LEG-3818 — translation.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3581 audit.py / LEG-3604 trading.py / LEG-3803 opaque densify family.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import translation as translation_mod
from src.api.routes.translation import (
    TranslationRequest,
    get_supported_languages,
    get_translations,
    get_user_language_preference,
    set_translation,
)


@pytest.mark.asyncio
async def test_get_supported_languages_unexpected_is_opaque_500():
    """get_supported_languages catch must not echo raw Exception text."""
    secret = "secret-languages-should-not-leak"
    svc = MagicMock()
    svc.get_supported_languages = AsyncMock(side_effect=RuntimeError(secret))

    with pytest.raises(HTTPException) as excinfo:
        await get_supported_languages(active_only=True, translation_service=svc)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_I18N_LANGUAGES_FAILED",
        "detail": "Failed to retrieve languages",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_translations_unexpected_is_opaque_500():
    """get_translations catch must not echo raw Exception text."""
    secret = "secret-translations-should-not-leak"
    svc = MagicMock()
    svc.get_translations = AsyncMock(side_effect=RuntimeError(secret))

    with pytest.raises(HTTPException) as excinfo:
        await get_translations(
            language_code="en",
            namespace=None,
            include_context=False,
            translation_service=svc,
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_I18N_TRANSLATIONS_GET_FAILED",
            "detail": "Failed to retrieve translations",
        }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_user_language_preference_unexpected_is_opaque_500():
    """get_user_language_preference catch must not echo raw Exception text."""
    secret = "secret-user-pref-should-not-leak"
    user = SimpleNamespace(id=uuid.uuid4())
    svc = MagicMock()
    svc.get_user_language_preference = AsyncMock(side_effect=RuntimeError(secret))

    with pytest.raises(HTTPException) as excinfo:
        await get_user_language_preference(current_user=user, translation_service=svc)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_I18N_PREF_GET_FAILED",
            "detail": "Failed to get language preference",
        }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_set_translation_unexpected_is_opaque_500():
    """set_translation catch must not echo raw Exception text."""
    secret = "secret-set-translation-should-not-leak"
    admin = SimpleNamespace(id=uuid.uuid4())
    svc = MagicMock()
    svc.db = MagicMock()
    svc.set_translation = AsyncMock(side_effect=RuntimeError(secret))
    request = TranslationRequest(key="common.greeting", value="Hello")

    with patch("src.api.routes.translation.log_admin_action"):
        with pytest.raises(HTTPException) as excinfo:
            await set_translation(
                language_code="en",
                namespace="common",
                request=request,
                admin_user=admin,
                translation_service=svc,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_I18N_SET_FAILED",
            "detail": "Failed to set translation",
        }
    assert secret not in str(exc.detail)


def test_translation_http500_catches_have_no_detail_str_e():
    """LEG-3818 — static pin: all twelve HTTP 500 catch paths stay opaque."""
    src = Path(translation_mod.__file__).read_text(encoding="utf-8")
    assert "route_internal_error" in src
    assert "ERR_I18N_TRANSLATIONS_GET_FAILED" in src
    assert "ERR_I18N_SET_FAILED" in src
    assert 'detail="Failed to retrieve translations"' not in src
    for stable in (
        "ERR_I18N_LANGUAGES_FAILED",
        # densified 'detail="Failed to retrieve translations"',
        # densified 'detail="Failed to get language preference"',
        # densified 'detail="Failed to set language preference"',
        # densified 'detail="Failed to get AI context"',
        # densified 'detail="Failed to get translation progress"',
        # densified 'detail="Failed to set translation"',
        # densified 'detail="Failed to import translations"',
        # densified 'detail="Failed to initialize translation data"',
    ):
        assert stable in src
    assert "detail=str(e)" not in src
    assert "Failed to retrieve languages: {str(e)}" not in src
    assert "Failed to retrieve translations: {str(e)}" not in src
    assert "Failed to get language preference: {str(e)}" not in src
    assert "Failed to set language preference: {str(e)}" not in src
    assert "Failed to get AI context: {str(e)}" not in src
    assert "Failed to get translation progress: {str(e)}" not in src
    assert "Failed to set translation: {str(e)}" not in src
    assert "Failed to import translations: {str(e)}" not in src
    assert "Failed to initialize translation data: {str(e)}" not in src
