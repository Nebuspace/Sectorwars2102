import logging
import time
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError, OperationalError
import traceback
import uuid

from src.core.config import settings
from src.core.security import get_password_hash
from src.models.user import User
from src.models.admin_credentials import AdminCredentials
from src.models.admin_scope_grant import AdminScopeGrant
from src.models.faction import Faction, FactionType
from src.auth.admin_scopes import META_SCOPES

logger = logging.getLogger(__name__)


def _ensure_meta_scope_grants(db: Session, user: User) -> int:
    """Insert missing active META_SCOPES grants for ``user`` (idempotent).

    Bootstrap / self-heal path for greenfield + existing default admin.
    Returns the number of rows inserted.
    """
    inserted = 0
    for scope in META_SCOPES:
        exists = (
            db.query(AdminScopeGrant.id)
            .filter(
                AdminScopeGrant.user_id == user.id,
                AdminScopeGrant.scope == scope,
                AdminScopeGrant.revoked_at.is_(None),
            )
            .first()
        )
        if exists:
            continue
        db.add(
            AdminScopeGrant(
                id=uuid.uuid4(),
                user_id=user.id,
                scope=scope,
                granted_by=user.id,  # self-grant (canon-allowed for bootstrap)
            )
        )
        inserted += 1
    return inserted


def create_default_admin(db: Session, max_retries: int = 3) -> None:
    """
    Create default admin user if it doesn't exist.

    RBAC Phase B: existence is keyed on username (NOT ``User.is_admin`` SQL,
    which is an EXISTS(active grant) expression — using that on a fresh
    deploy before grants exist caused a re-INSERT / IntegrityError boot-loop).
    Minting inserts the 3 META_SCOPES grants in the SAME transaction as the
    user + credentials (Max 2026-07-17: boot-bootstrap grants, don't just set
    the flag).
    """
    retry_count = 0
    admin_username = settings.ADMIN_USERNAME
    admin_password = settings.ADMIN_PASSWORD

    while retry_count < max_retries:
        try:
            # Username-only — avoids EXISTS boot-loop on greenfield.
            default_admin = db.query(User).filter(
                User.username == admin_username,
            ).first()
            
            if default_admin:
                logger.info(f"Default admin user '{admin_username}' already exists, skipping creation")

                # Sync flat flag + heal missing meta grants (greenfield self-heal).
                if not default_admin.is_admin:
                    default_admin.is_admin = True
                inserted = _ensure_meta_scope_grants(db, default_admin)
                if inserted:
                    logger.info(
                        "Healed %d missing meta-scope grant(s) for %s",
                        inserted,
                        admin_username,
                    )
                
                # Verify admin credentials also exist
                admin_creds = db.query(AdminCredentials).filter(
                    AdminCredentials.user_id == default_admin.id
                ).first()
                
                if not admin_creds:
                    logger.warning(f"Admin user exists but has no credentials! Creating credentials...")
                    try:
                        hashed_password = get_password_hash(admin_password)
                        admin_creds = AdminCredentials(
                            user_id=default_admin.id,
                            password_hash=hashed_password
                        )
                        db.add(admin_creds)
                    except Exception as e:
                        db.rollback()
                        logger.error(f"Failed to create credentials for existing admin: {str(e)}")
                        traceback.print_exc()
                        return

                try:
                    db.commit()
                except Exception as e:
                    db.rollback()
                    logger.error(f"Failed to commit default-admin heal: {str(e)}")
                
                return
            
            # Admin user doesn't exist, create it
            logger.info(f"Creating default admin user: {admin_username}")
            
            # Start a transaction
            try:
                # Create the user first
                admin = User(
                    id=uuid.uuid4(),  # Explicitly set UUID
                    username=admin_username,
                    email="admin@sectorwars2102.local",
                    is_admin=True,
                    is_active=True,
                    deleted=False
                )
                db.add(admin)
                db.flush()  # Flush but don't commit yet
                
                # Now create admin credentials
                admin_id = admin.id
                hashed_password = get_password_hash(admin_password)
                admin_creds = AdminCredentials(
                    user_id=admin_id,
                    password_hash=hashed_password
                )
                db.add(admin_creds)

                # 3 meta-scopes atomically with the user (sole greenfield path
                # to admin-hood alongside the flat flag).
                _ensure_meta_scope_grants(db, admin)
                
                # Commit the transaction
                db.commit()
                logger.info(
                    "Default admin user '%s' created with ID %s + %d meta scopes",
                    admin_username,
                    admin.id,
                    len(META_SCOPES),
                )
                
                if admin_username == "admin" and admin_password == "admin":
                    logger.warning(
                        "Default admin credentials are being used! "
                        "This is insecure and should be changed in production."
                    )
                
                # Successfully created admin, break out of the retry loop
                break
            except Exception as inner_e:
                db.rollback()
                logger.error(f"Transaction failed when creating admin: {str(inner_e)}")
                traceback.print_exc()
                raise  # Re-raise for outer exception handler
            
        except OperationalError as e:
            # Database connection error - retry after a delay
            retry_count += 1
            db.rollback()
            
            if retry_count >= max_retries:
                logger.error(f"Failed to create default admin after {max_retries} attempts: {str(e)}")
                break
                
            logger.warning(f"Database connection error, retrying ({retry_count}/{max_retries}): {str(e)}")
            time.sleep(2 * retry_count)  # Exponential backoff
            
        except SQLAlchemyError as e:
            logger.error(f"Database error creating default admin: {str(e)}")
            db.rollback()
            retry_count += 1
            time.sleep(1)  # Small delay before retry
            
        except Exception as e:
            logger.error(f"Unexpected error creating default admin: {str(e)}")
            traceback.print_exc()
            db.rollback()
            break


def create_default_player(db: Session) -> None:
    """
    This function is preserved for backward compatibility but no longer creates
    default players. Players should be registered through the registration flow
    or created by tests using proper test fixtures.
    """
    logger.info("Default player creation is disabled - players must register or be created by tests")
    return


def create_default_factions(db: Session, max_retries: int = 3) -> None:
    """
    Seed the canonical faction roster if rows are missing.

    Canon (FEATURES/gameplay/faction-lore.md: "codified in
    models/faction.py:FactionType and the seed data"; Status line "six
    allyable factions seeded plus Pirates"). Names, FactionType codes,
    aggression, and diplomacy stance follow faction-lore.md /
    factions-and-teams.md per faction.

    Roster scope (7 rows): the six allyable factions canon marks as seeded —
    Terran Federation (FEDERATION), Mercantile Guild (MERCHANTS), Frontier
    Coalition (INDEPENDENTS), Astral Mining Consortium (MINING, per ADR-0033),
    Nova Scientific Institute (EXPLORERS), Fringe Alliance (OUTLAWS) — plus
    the hostile-only Pirates (PIRATES).

    Deliberately NOT seeded (canon, not an oversight):
      - Shadow Syndicate (SYNDICATE): faction-lore.md "🚧 seed pending".
      - Galactic Concord (CONCORD): police-forces.md "📐 Design-only,
        operator-managed; not in the standard NPC-faction list".
      - The Cabal: 📐 Design-only and not present in the FactionType enum.
      - FactionType.MILITARY: not a canonical roster faction (no faction-lore
        entry); a legacy enum value retained per the model comment.

    Idempotent **per faction_type** (not "any rows exist"): the runtime
    npc_spawn_service._ensure_federation_faction may already have created the
    Terran Federation row before this seeder runs, so a blanket "skip if any
    factions exist" guard would leave the rest of the roster unseeded. Each
    row is created only if no row of that faction_type exists yet; an existing
    typed row (however named) is left untouched.

    Args:
        db: Database session
        max_retries: Maximum number of times to retry on database error
    """
    retry_count = 0

    # Canonical faction roster (faction-lore.md / factions-and-teams.md).
    factions_data = [
        {
            "name": "Terran Federation",
            "faction_type": FactionType.FEDERATION,
            "description": (
                "Earth's parliamentary government scaled to the settled "
                "volume. Order is the precondition for prosperity; maintains "
                "a standing navy and the Federation Bounty Board."
            ),
            "aggression_level": 4,
            "diplomacy_stance": "friendly",
            "color_primary": "#0066CC",
            "color_secondary": "#FFFFFF",
        },
        {
            "name": "Mercantile Guild",
            "faction_type": FactionType.MERCHANTS,
            "description": (
                "A neutral merchant cartel that built the inter-sector trade "
                "network. Charges the same prices to everyone — the manifest "
                "is the only thing that matters."
            ),
            "aggression_level": 2,
            "diplomacy_stance": "neutral",
            "color_primary": "#009900",
            "color_secondary": "#FFCC00",
        },
        {
            "name": "Frontier Coalition",
            "faction_type": FactionType.INDEPENDENTS,
            "description": (
                "Loose alliance of outer-rim colonies that broke from "
                "Federation oversight. Self-sufficient where possible, "
                "prickly where not."
            ),
            "aggression_level": 6,
            "diplomacy_stance": "neutral",
            "color_primary": "#FF9900",
            "color_secondary": "#333333",
        },
        {
            "name": "Astral Mining Consortium",
            "faction_type": FactionType.MINING,
            "description": (
                "The galaxy's largest extraction conglomerate. Owns mining "
                "rights on most resource-rich planets and belts; treats "
                "independent miners as competition."
            ),
            "aggression_level": 5,
            "diplomacy_stance": "neutral",
            "color_primary": "#8B5A2B",
            "color_secondary": "#333333",
        },
        {
            "name": "Nova Scientific Institute",
            "faction_type": FactionType.EXPLORERS,
            "description": (
                "A multi-discipline research institute studying warp-tunnel "
                "mechanics, quantum trade dynamics, and exotic-matter "
                "applications."
            ),
            "aggression_level": 3,
            "diplomacy_stance": "friendly",
            "color_primary": "#6600CC",
            "color_secondary": "#CCCCCC",
        },
        {
            "name": "Fringe Alliance",
            "faction_type": FactionType.OUTLAWS,
            "description": (
                "Loose confederation of smugglers, ex-pirates, refugees, and "
                "dissidents who reject Federation jurisdiction. Operates on a "
                "loose honor code — unlike the Pirates."
            ),
            "aggression_level": 7,
            "diplomacy_stance": "hostile",
            "color_primary": "#996600",
            "color_secondary": "#000000",
        },
        {
            "name": "Pirates",
            "faction_type": FactionType.PIRATES,
            "description": (
                "Anarchic raiders that exist as antagonists. Players cannot "
                "accumulate positive reputation with Pirates — only attack "
                "them."
            ),
            "aggression_level": 10,
            "diplomacy_stance": "hostile",
            "color_primary": "#CC0000",
            "color_secondary": "#000000",
        },
    ]

    while retry_count < max_retries:
        try:
            # Idempotent per faction_type: seed only the rows that don't yet
            # exist (the Federation row may already be present from the
            # runtime npc_spawn_service ensure path).
            existing_types = {
                row[0] for row in db.query(Faction.faction_type).all()
            }
            to_create = [
                fd for fd in factions_data
                if fd["faction_type"] not in existing_types
            ]
            if not to_create:
                logger.info(
                    "Faction roster already seeded (%d of %d canonical types "
                    "present); skipping",
                    len(existing_types), len(factions_data),
                )
                return

            logger.info(
                "Seeding %d missing canonical faction(s)...", len(to_create)
            )

            # Create factions in a transaction
            try:
                created_count = 0
                for faction_data in to_create:
                    faction = Faction(**faction_data)
                    db.add(faction)
                    created_count += 1
                    logger.debug(f"Added faction: {faction.name}")

                db.commit()
                logger.info(f"Successfully created {created_count} default factions")
                break
                
            except Exception as inner_e:
                db.rollback()
                logger.error(f"Transaction failed when creating factions: {str(inner_e)}")
                raise  # Re-raise for outer exception handler
            
        except OperationalError as e:
            # Database connection error - retry after a delay
            retry_count += 1
            db.rollback()
            
            if retry_count >= max_retries:
                logger.error(f"Failed to create default factions after {max_retries} attempts: {str(e)}")
                break
                
            logger.warning(f"Database connection error, retrying ({retry_count}/{max_retries}): {str(e)}")
            time.sleep(2 * retry_count)  # Exponential backoff
            
        except SQLAlchemyError as e:
            logger.error(f"Database error creating default factions: {str(e)}")
            db.rollback()
            retry_count += 1
            time.sleep(1)  # Small delay before retry
            
        except Exception as e:
            logger.error(f"Unexpected error creating default factions: {str(e)}")
            traceback.print_exc()
            db.rollback()
            break