"""LEG-3994: _raise_for maps ContractForbiddenError → HTTP 403."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes import contracts as contracts_routes
from src.services.contract_service import (
    ContractConflictError,
    ContractError,
    ContractForbiddenError,
    ContractNotFoundError,
)


def test_raise_for_forbidden_is_403() -> None:
    with pytest.raises(HTTPException) as ei:
        contracts_routes._raise_for(
            ContractForbiddenError("hostility: cannot accept")
        )
    assert ei.value.status_code == 403
    assert str(ei.value.detail).startswith("hostility:")


def test_raise_for_blocklisted_is_403() -> None:
    with pytest.raises(HTTPException) as ei:
        contracts_routes._raise_for(
            ContractForbiddenError("blocklisted: cannot accept")
        )
    assert ei.value.status_code == 403
    assert str(ei.value.detail).startswith("blocklisted:")


def test_raise_for_preserves_other_classes() -> None:
    with pytest.raises(HTTPException) as ei:
        contracts_routes._raise_for(ContractNotFoundError("missing"))
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        contracts_routes._raise_for(ContractConflictError("stale"))
    assert ei.value.status_code == 409
    with pytest.raises(HTTPException) as ei:
        contracts_routes._raise_for(ContractError("bad"))
    assert ei.value.status_code == 400
