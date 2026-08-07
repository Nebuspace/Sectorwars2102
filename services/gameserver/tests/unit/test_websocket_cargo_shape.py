"""WO-FIX-ENHANCED-WEBSOCKET-CARGO-SHAPE-MISMATCH — AST pin.

The websocket trade path must mutate nested Ship.cargo
``{capacity, used, contents}``, never flat top-level commodity keys.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "services"
    / "enhanced_websocket_service.py"
)


def test_websocket_trade_writes_nested_contents_not_flat_keys():
    tree = ast.parse(_SRC.read_text())
    method = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_execute_trade"
    )
    text = ast.get_source_segment(_SRC.read_text(), method) or ""
    assert 'cargo["contents"]' in text or "cargo['contents']" in text
    assert "flag_modified" in text
    assert "effective_cargo_capacity" in text
    assert "current_ship.cargo[commodity]" not in text
    assert "sum(current_ship.cargo.values()" not in text
