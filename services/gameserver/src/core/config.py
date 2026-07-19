import logging
import os
from typing import Optional
from cryptography.fernet import Fernet
from pydantic import PostgresDsn, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

# Note: DATABASE_URL validation will happen in the Settings class below
# which properly loads from .env files using Pydantic


class Settings(BaseSettings):
    # Base
    API_BASE_URL: str = os.environ.get("API_BASE_URL", "")  # Empty string to auto-detect
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "development")
    DEBUG: bool = os.environ.get("DEBUG", "False").lower() == "true"
    
    # Test and development mode flags
    TESTING: bool = os.environ.get("TESTING", "False").lower() == "true"
    DEVELOPMENT_MODE: bool = os.environ.get("ENVIRONMENT", "development").lower() == "development"
    
    # Security Configuration - CRITICAL: These MUST be set in production
    JWT_SECRET: str = os.environ.get("JWT_SECRET")  # No default - MUST be set
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))  # Reduced to 1 hour
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))  # Reduced to 7 days

    # ARIA personal-memory encryption key (WO-DRIFT-aria-rt-mem-encryption-
    # key). Same discipline as JWT_SECRET above: a persistent, stack-loaded
    # secret, never a per-boot/per-instantiation generated throwaway -- a
    # rotating key would permanently orphan every previously-encrypted
    # ARIAPersonalMemory row (Fernet raises InvalidToken decrypting under a
    # different key). No default -- MUST be set. Value is a url-safe
    # base64-encoded 32-byte Fernet key, e.g. the output of
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    ARIA_ENCRYPTION_KEY: str = os.environ.get("ARIA_ENCRYPTION_KEY")  # No default - MUST be set

    # Admin credentials - CRITICAL: These MUST be set in production
    ADMIN_USERNAME: str = os.environ.get("ADMIN_USERNAME")  # No default - MUST be set
    ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD")  # No default - MUST be set
    
    def __init__(self, **kwargs):
        """Initialize settings with security validation"""
        super().__init__(**kwargs)
        self._validate_security_config()
    
    def _validate_security_config(self):
        """Validate critical security configuration"""
        if not self.JWT_SECRET:
            raise ValueError("JWT_SECRET environment variable is required for security")
        if len(self.JWT_SECRET) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters for security")
        if not self.ARIA_ENCRYPTION_KEY:
            raise ValueError("ARIA_ENCRYPTION_KEY environment variable is required for security")
        try:
            # Validate with the exact constructor the service uses, so
            # "valid at boot" == "usable by ARIAPersonalIntelligenceService"
            # -- a malformed key (trailing newline, truncated paste, wrong
            # length) must fail loud HERE, not lazily as a confusing 500 on
            # the first ARIA-touching request. Never include the key value
            # itself in the error message.
            Fernet(self.ARIA_ENCRYPTION_KEY)
        except Exception:
            raise ValueError(
                "ARIA_ENCRYPTION_KEY must be a valid url-safe base64-encoded 32-byte Fernet key"
            ) from None
        if not self.ADMIN_USERNAME:
            raise ValueError("ADMIN_USERNAME environment variable is required")
        if not self.ADMIN_PASSWORD:
            raise ValueError("ADMIN_PASSWORD environment variable is required")
        if len(self.ADMIN_PASSWORD) < 12:
            raise ValueError("ADMIN_PASSWORD must be at least 12 characters for security")
        if "dev_only_not_for_production" in self.REDIS_URL:
            logger.warning(
                "SECURITY WARNING: REDIS_URL is using the default dev-only password. "
                "Set REDIS_URL with a strong password for production deployments."
            )
        # WO-ARIA-PROMPT-DEFENSE addendum: a runtime tripwire for the exact
        # unsafe combination flagged in that WO's NO-CANON #1 -- LLM chat
        # live with the load-bearing content classifiers (ADR-0057 A-V1
        # layers 3+5) still dark. Deliberately a WARN, not a raise: the two
        # flags stay independently togglable by design (coupling them would
        # break the mock-isolation this WO's test suite depends on), so a
        # deploy CAN legitimately run this combination during staged
        # rollout -- it just must never be mistaken for "fully protected."
        # "ARIA-DEFENSE-MISCONFIG" is a deliberately greppable token for
        # log-based alerting.
        if self.ARIA_LLM_CHAT_ENABLED and not self.ARIA_PROMPT_CLASSIFIER_ENABLED:
            logger.warning(
                "ARIA-DEFENSE-MISCONFIG: ARIA_LLM_CHAT_ENABLED is true but "
                "ARIA_PROMPT_CLASSIFIER_ENABLED is false. ARIA's LLM chat "
                "path is LIVE without the load-bearing input/output content "
                "classifiers (ADR-0057 A-V1 layers 3+5) -- only the cheap "
                "pre-filters (NFKC normalization, JSON-envelope breakout "
                "detection, versioned pattern list) are protecting it. "
                "Go-live requires BOTH flags set true together."
            )
    
    # AI Provider Configuration
    OPENAI_API_KEY: Optional[str] = os.environ.get("OPENAI_API_KEY")
    ANTHROPIC_API_KEY: Optional[str] = os.environ.get("ANTHROPIC_API_KEY")
    AI_PROVIDER_PRIMARY: str = os.environ.get("AI_PROVIDER_PRIMARY", "openai")
    AI_PROVIDER_SECONDARY: str = os.environ.get("AI_PROVIDER_SECONDARY", "anthropic")
    AI_PROVIDER_FALLBACK: str = os.environ.get("AI_PROVIDER_FALLBACK", "manual")
    
    # AI Model Configuration
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")
    ANTHROPIC_MODEL: str = os.environ.get("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")
    AI_DIALOGUE_ENABLED: bool = os.environ.get("AI_DIALOGUE_ENABLED", "true").lower() == "true"

    # ARIA LLM-backed chat (WO-ARIA-CHAT-LLM). BUILT DARK per Max's GO:
    # defaults false, so ARIA's chat path stays byte-identical to the
    # existing keyword/template engine until this is explicitly flipped —
    # zero spend, zero behavior change, until then.
    ARIA_LLM_CHAT_ENABLED: bool = os.environ.get("ARIA_LLM_CHAT_ENABLED", "false").lower() == "true"

    # ADR-0057 A-V1 layers 3+5 -- the load-bearing input/output content
    # classifiers wrapping the LLM provider call (WO-ARIA-PROMPT-DEFENSE).
    # BUILT DARK, same convention as ARIA_LLM_CHAT_ENABLED above: defaults
    # false so a pre-existing/unmocked test exercising _try_llm_chat_
    # response never risks a real classifier provider call. [NO-CANON] --
    # this is a SEPARATE flag from ARIA_LLM_CHAT_ENABLED, not a
    # sub-setting of it. Per this WO's own mission ("never LLM chat on
    # regex-only defense"), operational go-live MUST flip this ALONGSIDE
    # (or before) ARIA_LLM_CHAT_ENABLED -- flagged prominently for the
    # orchestrator/Max, since two independent flags both defaulting off
    # creates exactly the gap the WO exists to close if only one is ever
    # flipped. Layers 1/2/4 (NFKC, envelope, pattern-list) are NOT gated
    # by this flag -- they are cheap, local, and always on.
    ARIA_PROMPT_CLASSIFIER_ENABLED: bool = os.environ.get("ARIA_PROMPT_CLASSIFIER_ENABLED", "false").lower() == "true"

    # Living NPC System — gates the npc_scheduler_service lifespan task
    # (Loops A/B/C). Default off so prod stays static until proven on dev.
    NPC_SCHEDULER_ENABLED: bool = os.environ.get("NPC_SCHEDULER_ENABLED", "false").lower() == "true"

    # PayPal Configuration
    PAYPAL_CLIENT_ID: str = os.environ.get("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET: str = os.environ.get("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_GALACTIC_CITIZEN_PLAN_ID: str = os.environ.get("PAYPAL_GALACTIC_CITIZEN_PLAN_ID", "")
    PAYPAL_REGIONAL_OWNER_PLAN_ID: str = os.environ.get("PAYPAL_REGIONAL_OWNER_PLAN_ID", "")
    PAYPAL_NEXUS_PREMIUM_PLAN_ID: str = os.environ.get("PAYPAL_NEXUS_PREMIUM_PLAN_ID", "")
    PAYPAL_WEBHOOK_ID: str = os.environ.get("PAYPAL_WEBHOOK_ID", "")

    # Development Environment Type
    DEV_ENVIRONMENT: str = os.environ.get("DEV_ENVIRONMENT", "")  # local, codespaces
    NODE_ENV: Optional[str] = os.environ.get("NODE_ENV")
    FRONTEND_URL: Optional[str] = os.environ.get("FRONTEND_URL")
    CODESPACE_NAME: Optional[str] = os.environ.get("CODESPACE_NAME")
    CLIENT_ID_GITHUB: Optional[str] = os.environ.get("CLIENT_ID_GITHUB")
    CLIENT_SECRET_GITHUB: Optional[str] = os.environ.get("CLIENT_SECRET_GITHUB")
    
    # Important: GitHub OAuth variables must not start with GITHUB_ as GitHub reserves this prefix
    # for their own environment variables in GitHub Actions and Codespaces
    @property
    def GITHUB_CLIENT_ID(self) -> Optional[str]:
        """For backward compatibility. Please use CLIENT_ID_GITHUB instead."""
        return self.CLIENT_ID_GITHUB
        
    @property
    def GITHUB_CLIENT_SECRET(self) -> Optional[str]:
        """For backward compatibility. Please use CLIENT_SECRET_GITHUB instead."""
        return self.CLIENT_SECRET_GITHUB


    # Database
    DATABASE_URL: PostgresDsn = Field(
        description="PostgreSQL database URL"
    )
    DATABASE_TEST_URL: Optional[PostgresDsn] = Field(
        default=None,
        description="PostgreSQL test database URL (optional)"
    )
    DATABASE_URL_PROD: Optional[PostgresDsn] = None
    SQLALCHEMY_POOL_SIZE: int = 10
    SQLALCHEMY_MAX_OVERFLOW: int = 20
    
    # Redis Configuration
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://:dev_only_not_for_production@localhost:6379/0")
    REDIS_CACHE_TTL: int = int(os.environ.get("REDIS_CACHE_TTL", "3600"))  # 1 hour default
    REDIS_SESSION_TTL: int = int(os.environ.get("REDIS_SESSION_TTL", "86400"))  # 24 hours default

    def detect_environment(self) -> str:
        """Detect the development environment type."""
        # If explicitly set, use that
        if self.DEV_ENVIRONMENT:
            return self.DEV_ENVIRONMENT

        # Check for GitHub Codespaces
        if os.environ.get("CODESPACE_NAME") or os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"):
            return "codespaces"

        # Default to local
        return "local"

    def get_api_base_url(self) -> str:
        """Get the appropriate API base URL based on environment."""
        # If explicitly set through environment variable, use that
        if self.API_BASE_URL:
            return self.API_BASE_URL

        # Auto-detect based on environment
        env_type = self.detect_environment()

        if env_type == "codespaces":
            # For Codespaces, construct URL from environment variables
            codespace_name = os.environ.get("CODESPACE_NAME")
            if codespace_name:
                # Include port in Codespaces URL as the port is embedded in the hostname
                return f"https://{codespace_name}-8080.app.github.dev"

        # Default for local development
        return "http://localhost:8080"

    def get_frontend_url(self) -> str:
        """Get the appropriate frontend URL based on environment."""
        # If explicitly set through FRONTEND_URL environment variable, use that
        if os.environ.get("FRONTEND_URL"):
            frontend_url = os.environ.get("FRONTEND_URL")
            logger.debug("Using explicitly set FRONTEND_URL from environment")
            return frontend_url

        # Auto-detect based on environment
        env_type = self.detect_environment()
        logger.debug("Auto-detecting frontend URL for environment: %s", env_type)

        if env_type == "codespaces":
            # For Codespaces, construct URL from environment variables
            # Make sure we handle any path proxying that might be happening in GitHub Codespaces
            codespace_name = os.environ.get("CODESPACE_NAME")
            github_codespaces_port_forwarding_domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")

            if codespace_name and github_codespaces_port_forwarding_domain:
                # Full domain format with official GitHub Codespaces domain
                # Include port in the hostname for Codespaces URLs
                frontend_url = f"https://{codespace_name}-3000.{github_codespaces_port_forwarding_domain}"
                logger.debug("Using modern Codespaces URL format")
                return frontend_url
            elif codespace_name:
                # Legacy/default format with port in the hostname
                frontend_url = f"https://{codespace_name}-3000.app.github.dev"
                logger.debug("Using legacy Codespaces URL format")
                return frontend_url
            else:
                logger.warning("In Codespaces environment but CODESPACE_NAME not set")

        elif env_type == "replit":
            # For Replit, derive from the API URL but on port 3000
            # This assumes player-client is on port 3000
            repl_slug = os.environ.get("REPL_SLUG")
            repl_owner = os.environ.get("REPL_OWNER")
            if repl_slug and repl_owner:
                frontend_url = f"https://{repl_slug}.{repl_owner}.repl.co:3000"
                logger.debug("Using Replit URL format")
                return frontend_url

        # Default for local development
        frontend_url = "http://localhost:3000"
        logger.debug("Using default frontend URL: localhost:3000")
        return frontend_url

    def get_db_url(self) -> str:
        """Get the appropriate database URL based on environment."""
        # Ensure correct type casting for Pydantic DSNs
        if self.ENVIRONMENT == "testing" and self.DATABASE_TEST_URL:
            db_url = str(self.DATABASE_TEST_URL)
        elif self.ENVIRONMENT == "production" and self.DATABASE_URL_PROD:
            db_url = str(self.DATABASE_URL_PROD)
        else:
            db_url = str(self.DATABASE_URL)
        
        # Add endpoint parameter for Neon databases if not already present
        if "neon.tech" in db_url and "options=endpoint" not in db_url:
            # Extract the endpoint ID from the host
            import re
            match = re.search(r'@(ep-[a-z0-9-]+)', db_url)
            if match:
                endpoint_id = match.group(1)
                # Add or append to existing parameters
                if "?" in db_url:
                    db_url += f"&options=endpoint%3D{endpoint_id}"
                else:
                    db_url += f"?options=endpoint%3D{endpoint_id}"
        
        return db_url

    # Using model_config for newer Pydantic versions
    model_config = {
        "env_file": ["../../.env", ".env"],  # Look in parent directory first, then current
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Ignore extra fields from .env
    }

# Load .env file if DATABASE_URL not in environment
if not os.environ.get("DATABASE_URL"):
    import pathlib
    from dotenv import load_dotenv
    
    # Try to load from parent directory .env file
    env_path = pathlib.Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

# Create settings instance
settings = Settings()

def get_config() -> Settings:
    """Get the configuration settings instance."""
    return settings