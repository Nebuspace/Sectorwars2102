"""Canon pins for quantum-scan cooldown + misread texture (cycle-28).

WO-FIX-QUANTUM-SCAN-COOLDOWN-SCALED: scan cooldown is REAL hours, not scaled.
WO-FIX-QUANTUM-MISREAD-TEXTURE-SWAP: misread fuzzes resonance only.
"""
from __future__ import annotations

from pathlib import Path

from src.services import quantum_service as qs


def test_scan_cooldown_constant_is_unscaled_real_hours():
    assert qs.SCAN_COOLDOWN_HOURS == 4.0
    src = Path(qs.__file__).read_text()
    assert "quantum_scan_cooldown_until = scaled_deadline(SCAN_COOLDOWN_HOURS)" not in src
    assert "timedelta(hours=SCAN_COOLDOWN_HOURS)" in src


def test_misread_block_does_not_shift_texture():
    src = Path(qs.__file__).read_text()
    idx = src.find("MISREAD_BASE_PCT - MISREAD_REDUCTION_PER_SENSOR_LEVEL")
    assert idx > 0
    chunk = src[idx : idx + 800]
    assert "RESONANCE_ORDER.index(resonance)" in chunk
    assert "TEXTURE_ORDER.index(texture)" not in chunk
    assert "texture = TEXTURE_ORDER" not in chunk
