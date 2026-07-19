"""WO-DRIFT-aria-rt-mem-encryption-key -- ARIA personal-memory encryption
key must be a persistent, stack-loaded secret (JWT_SECRET-grade fail-loud
discipline), never a per-boot/per-instantiation Fernet.generate_key()
throwaway.

Pre-fix, ``ARIAPersonalIntelligenceService._initialize_encryption`` read:
    key = settings.ARIA_ENCRYPTION_KEY if hasattr(settings, 'ARIA_ENCRYPTION_KEY') else Fernet.generate_key()
``hasattr`` on a ``pydantic_settings.BaseSettings`` instance is False for any
undeclared field (``extra="ignore"`` never surfaces it as an attribute), and
``ARIA_ENCRYPTION_KEY`` was never declared on ``Settings`` -- so the
``hasattr`` branch was ALWAYS false and every single instantiation of the
service minted a brand-new random key. Two instances (e.g. across a
restart, or the second ``ARIAPersonalIntelligenceService()`` scheduler/
presence_helpers.py constructs outside the ``get_aria_intelligence_service()``
singleton) could never decrypt each other's rows.

DB-free throughout -- ``Settings(**kwargs)`` direct construction mirrors
``TestAriaDefenseMisconfigTripwire`` in test_aria_prompt_defense.py, and
``_encrypt_memory``/``_decrypt_memory`` are plain sync methods with no DB.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

import src.services.aria_personal_intelligence_service as aria_svc_module
from src.core.config import Settings
from src.services.aria_personal_intelligence_service import (
    ARIAPersonalIntelligenceService,
)


def _settings_kwargs(**overrides):
    """The three fields _validate_security_config hard-requires besides
    ARIA_ENCRYPTION_KEY -- supplied directly as constructor kwargs so this
    test never depends on the shell's actual environment (same pattern as
    TestAriaDefenseMisconfigTripwire._env_kwargs in test_aria_prompt_defense.py)."""
    kwargs = dict(
        JWT_SECRET="test_jwt_secret_at_least_32_characters_long",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="test_admin_password_12plus",
    )
    kwargs.update(overrides)
    return kwargs


@pytest.mark.unit
class TestAriaEncryptionKeyFailLoud:
    """Config-level gate: mirrors JWT_SECRET's presence check exactly."""

    def test_absent_key_raises_loud_not_silent(self):
        # Pass the empty string explicitly rather than omitting the kwarg:
        # `ARIA_ENCRYPTION_KEY: str = os.environ.get(...)` bakes its
        # class-level default at config.py's IMPORT time (this test
        # harness's own shell exports a real key so the module can import
        # at all), so an *omitted* kwarg would fall through to that stale
        # baked-in value, not a genuinely-absent one. An explicit "" is
        # exactly what `not self.ARIA_ENCRYPTION_KEY` treats as absent --
        # the same falsy check JWT_SECRET uses two lines above it.
        with pytest.raises(ValueError, match="ARIA_ENCRYPTION_KEY"):
            Settings(**_settings_kwargs(ARIA_ENCRYPTION_KEY=""))

    def test_malformed_key_raises_at_boot_not_lazily_at_first_use(self):
        """A PRESENT-but-malformed key (realistic on a hand-pasted-onto-3-
        hosts rollout: truncated paste, trailing newline, wrong length)
        must fail loud at Settings() construction (boot), matching this
        WO's own fail-loud thesis -- not lazily as a confusing 500 on the
        first ARIA-touching request when ARIAPersonalIntelligenceService()
        first instantiates Fernet(bad_key)."""
        with pytest.raises(ValueError, match="ARIA_ENCRYPTION_KEY"):
            Settings(**_settings_kwargs(ARIA_ENCRYPTION_KEY="not-a-valid-fernet-key"))

    def test_present_key_does_not_raise(self):
        fixed_key = Fernet.generate_key().decode()
        # Should not raise.
        Settings(**_settings_kwargs(ARIA_ENCRYPTION_KEY=fixed_key))


@pytest.mark.unit
class TestAriaEncryptionKeyPersistsAcrossRestart:
    """Service-level regression pin: a FIXED key must produce a stable
    encrypt -> decrypt round-trip across a simulated restart (a fresh
    ``ARIAPersonalIntelligenceService()`` instance, same env key)."""

    def test_fixed_key_round_trips_across_simulated_restart(self, monkeypatch):
        fixed_key = Fernet.generate_key().decode()
        fresh_settings = Settings(**_settings_kwargs(ARIA_ENCRYPTION_KEY=fixed_key))
        # Patch the module-level `settings` name the service module resolves
        # `settings.ARIA_ENCRYPTION_KEY` against (its own `from
        # src.core.config import settings` binding) -- never mutate the real
        # process-wide singleton.
        monkeypatch.setattr(aria_svc_module, "settings", fresh_settings)

        content = {"secret": "trade route to Sol", "value": 42}

        instance_a = ARIAPersonalIntelligenceService()
        encrypted = instance_a._encrypt_memory(content)

        # Simulate a restart: a brand-new instance, same persistent env key.
        instance_b = ARIAPersonalIntelligenceService()
        decrypted = instance_b._decrypt_memory(encrypted)

        assert decrypted == content

    def test_mismatched_keys_do_not_round_trip(self, monkeypatch):
        """Negative control -- proves the round-trip assertion above is
        actually discriminating (not vacuously true): two DIFFERENT keys
        across the "restart" must fail to decrypt, exactly like the
        pre-fix per-instantiation-random-key bug."""
        key_a = Fernet.generate_key().decode()
        key_b = Fernet.generate_key().decode()

        monkeypatch.setattr(
            aria_svc_module,
            "settings",
            Settings(**_settings_kwargs(ARIA_ENCRYPTION_KEY=key_a)),
        )
        instance_a = ARIAPersonalIntelligenceService()
        encrypted = instance_a._encrypt_memory({"secret": "trade route to Sol"})

        monkeypatch.setattr(
            aria_svc_module,
            "settings",
            Settings(**_settings_kwargs(ARIA_ENCRYPTION_KEY=key_b)),
        )
        instance_b = ARIAPersonalIntelligenceService()

        with pytest.raises(InvalidToken):
            instance_b._decrypt_memory(encrypted)
