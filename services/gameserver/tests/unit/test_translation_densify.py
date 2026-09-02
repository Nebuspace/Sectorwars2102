"""LEG-3846 — translation public endpoints unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes import translation as translation_mod
from src.api.routes.translation import detect_language, get_supported_languages


@pytest.mark.asyncio
async def test_get_supported_languages_unexpected_returns_structured_500():
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
async def test_detect_language_unexpected_returns_structured_500():
    secret = "secret-detect-should-not-leak"
    svc = MagicMock()
    svc.detect_user_language = AsyncMock(side_effect=RuntimeError(secret))

    with pytest.raises(HTTPException) as excinfo:
        await detect_language(
            request=SimpleNamespace(),
            accept_language="en-US",
            translation_service=svc,
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_I18N_DETECT_FAILED",
        "detail": "Failed to detect language",
    }
    assert secret not in str(exc.detail)


def test_translation_public_http500_catches_are_structured():
    """LEG-3846 — static pin: public i18n 500 catch paths emit error_code + detail."""
    src = Path(translation_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_I18N_LANGUAGES_FAILED",
        "ERR_I18N_DETECT_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert "ERR_I18N_DETECT_FAILED" in src
    # Public handlers only — admin get_all_languages still uses bare HTTPException (out of scope).
    public_block = src.split("@router.get(\"/detect\")")[0]
    assert "route_internal_error(ERR_I18N_LANGUAGES_FAILED" in public_block
