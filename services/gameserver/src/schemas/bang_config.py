"""Pydantic schema for the `sw2102-bang` CLI invocation contract.

This is the *shared* config object passed from the admin form → job row's
`params_json` → bang's `--config-json` stdin. Both `BangImportService`
(translator) and `BangGenerationJob` model serialize/deserialize through
this schema, so any field added here is automatically available on both
ends.

The field set mirrors bang v1.3.0's `BigBangConfig` plus the three
gameserver-driven knobs (`region_type`, `sectors`, `seed`). Most fields are
optional with sane defaults — bang itself supplies defaults if the field is
absent from the JSON payload.

See `DOCS/PLANS/bang-integration.md` § "Phase 1B" + bang's `src/types.ts`
for upstream field semantics.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

RegionType = Literal["player_owned", "terran_space", "central_nexus"]


class BangConfig(BaseModel):
    """Generation parameters for a single bang region invocation.

    The translator instantiates one `BangConfig` per region in a multi-region
    job (player_owned + terran_space + central_nexus). The seed is shared
    across all three; region-specific fields (sector count) differ per region.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- Required identity / region knobs (gameserver-driven) ---
    seed: int = Field(
        ...,
        ge=0,
        description=(
            "uint64-range positive seed. Bang accepts as JS number; we store "
            "as BIGINT on Galaxy.bang_seed for precision."
        ),
    )
    sectors: int = Field(
        ...,
        ge=20,
        le=20_000,
        description="Sector count for this region (20..20000).",
    )
    region_type: RegionType = Field(
        ...,
        description=(
            "Which region this invocation builds: player_owned (variable), "
            "terran_space (300 sectors), or central_nexus (5000 sectors)."
        ),
    )

    # --- Optional bang CLI flags (all match BigBangConfig field names) ---
    federation_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percent of sectors in Federation zone.",
    )
    border_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percent of sectors in Border zone.",
    )
    frontier_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percent of sectors in Frontier zone.",
    )

    port_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percent of sectors that host a Port/Station.",
    )
    planet_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percent of sectors that host at least one Planet.",
    )
    nebula_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percent of sectors that are nebula sectors.",
    )

    max_warps: Optional[int] = Field(
        default=None,
        ge=1,
        le=12,
        description="Maximum outbound warps per sector (cluster cap).",
    )
    one_way_warp_percent: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percent of warps that are one-way (TW2002 style).",
    )

    # --- Expert / dev toggles ---
    validator_strictness: Optional[Literal["lenient", "standard", "strict"]] = (
        Field(
            default=None,
            description=(
                "Bang's Phase-13 validator mode. `strict` fails on any "
                "TOPOLOGY_RESCUE; `lenient` warns only."
            ),
        )
    )
    stardock_enabled: Optional[bool] = Field(
        default=None,
        description="Whether the StarDock special location is placed.",
    )
