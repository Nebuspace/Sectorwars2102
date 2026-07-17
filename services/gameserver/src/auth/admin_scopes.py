"""Admin scope catalog — RBAC Phase A1 (ADR-0058 A-F2) + Max-ruled expansion.

ADR-0058's 19 platform scopes are VERBATIM.  Operational scopes (Max-ruled
19→26 expansion) are listed below.  GRANTS live as AdminScopeGrant rows;

HIGH_IMPACT subset: actions that carry material financial, access, or
structural risk — surfaced to the daily review queue (Phase E) and subject to
the two-eyes retrospective ack.
"""

# ---------------------------------------------------------------------------
# The 19 canonical scopes
# ---------------------------------------------------------------------------

# Player management
PLAYERS_VIEW = "admin.players.view"
PLAYERS_SUSPEND = "admin.players.suspend"
PLAYERS_ADJUST_REP = "admin.players.adjust_rep"
PLAYERS_TRANSFER_ASSETS = "admin.players.transfer_assets"

# Subscription management
SUBSCRIPTIONS_VIEW = "admin.subscriptions.view"
SUBSCRIPTIONS_MODIFY = "admin.subscriptions.modify"
SUBSCRIPTIONS_REFUND = "admin.subscriptions.refund"

# Webhook management
WEBHOOKS_VIEW = "admin.webhooks.view"
WEBHOOKS_REPLAY = "admin.webhooks.replay"

# Region management
REGIONS_VIEW = "admin.regions.view"
REGIONS_CREATE = "admin.regions.create"
REGIONS_TERMINATE = "admin.regions.terminate"
REGIONS_TRANSFER_OWNERSHIP = "admin.regions.transfer_ownership"

# ARIA / AI oversight
ARIA_AUDIT = "admin.aria.audit"

# Multi-account review
MULTI_ACCOUNT_REVIEW = "admin.multi_account.review"

# Galaxy generation
BANG_REGENERATE = "admin.bang.regenerate"

# Meta-scopes: scope management + audit
SCOPES_GRANT = "admin.scopes.grant"
SCOPES_REVOKE = "admin.scopes.revoke"
AUDIT_VIEW = "admin.audit.view"

# Operational (Max-ruled catalog expansion 19→26 — see
# audit/design-briefs/rbac-scope-expansion-2026-07-17.md).
GALAXY_MANAGE = "admin.galaxy.manage"
PLAYERS_ADJUST_CREDITS = "admin.players.adjust_credits"
SHIPS_MANAGE = "admin.ships.manage"
COMBAT_INTERVENE = "admin.combat.intervene"
ECONOMY_INTERVENE = "admin.economy.intervene"
SECURITY_ACT = "admin.security.act"
DISPUTES_RESOLVE = "admin.disputes.resolve"

# ---------------------------------------------------------------------------
# Derived sets
# ---------------------------------------------------------------------------

ALL_SCOPES: frozenset[str] = frozenset(
    {
        PLAYERS_VIEW,
        PLAYERS_SUSPEND,
        PLAYERS_ADJUST_REP,
        PLAYERS_TRANSFER_ASSETS,
        SUBSCRIPTIONS_VIEW,
        SUBSCRIPTIONS_MODIFY,
        SUBSCRIPTIONS_REFUND,
        WEBHOOKS_VIEW,
        WEBHOOKS_REPLAY,
        REGIONS_VIEW,
        REGIONS_CREATE,
        REGIONS_TERMINATE,
        REGIONS_TRANSFER_OWNERSHIP,
        ARIA_AUDIT,
        MULTI_ACCOUNT_REVIEW,
        BANG_REGENERATE,
        SCOPES_GRANT,
        SCOPES_REVOKE,
        AUDIT_VIEW,
        GALAXY_MANAGE,
        PLAYERS_ADJUST_CREDITS,
        SHIPS_MANAGE,
        COMBAT_INTERVENE,
        ECONOMY_INTERVENE,
        SECURITY_ACT,
        DISPUTES_RESOLVE,
    }
)

# Actions that carry material financial, access, or structural risk.
# These are surfaced to the daily review queue (Phase E) for retrospective ack.
HIGH_IMPACT_SCOPES: frozenset[str] = frozenset(
    {
        # ADR-0058 / Phase E review-queue surface: subscriptions.* ·
        # webhooks.replay · regions.terminate · scopes.*
        SUBSCRIPTIONS_VIEW,
        SUBSCRIPTIONS_MODIFY,
        SUBSCRIPTIONS_REFUND,
        WEBHOOKS_REPLAY,
        REGIONS_TERMINATE,
        SCOPES_GRANT,
        SCOPES_REVOKE,
        GALAXY_MANAGE,
        PLAYERS_ADJUST_CREDITS,
        SHIPS_MANAGE,
        DISPUTES_RESOLVE,
    }
)

# Bootstrap superadmin needs these 3 meta-scopes so a single-operator
# deployment can always reach the grant/revoke surface.
META_SCOPES: frozenset[str] = frozenset(
    {
        SCOPES_GRANT,
        SCOPES_REVOKE,
        AUDIT_VIEW,
    }
)

# Human-readable catalog text for GET /admin/scopes/catalog (Phase D).
SCOPE_DESCRIPTIONS: dict[str, str] = {
    PLAYERS_VIEW: "View player profiles and read-only player admin data.",
    PLAYERS_SUSPEND: "Suspend or unsuspend player accounts.",
    PLAYERS_ADJUST_REP: "Adjust player faction reputation scores.",
    PLAYERS_TRANSFER_ASSETS: "Transfer assets between players.",
    SUBSCRIPTIONS_VIEW: "View PayPal subscription records and billing status.",
    SUBSCRIPTIONS_MODIFY: "Modify subscription tier or subscription status.",
    SUBSCRIPTIONS_REFUND: "Issue subscription refunds.",
    WEBHOOKS_VIEW: "View webhook delivery logs and payload history.",
    WEBHOOKS_REPLAY: "Replay failed or missing webhook delivery events.",
    REGIONS_VIEW: "View game region configuration and metadata.",
    REGIONS_CREATE: "Create new game regions.",
    REGIONS_TERMINATE: "Terminate or decommission game regions.",
    REGIONS_TRANSFER_OWNERSHIP: "Transfer region ownership between users.",
    ARIA_AUDIT: "Audit ARIA AI dialogue sessions and model interactions.",
    MULTI_ACCOUNT_REVIEW: "Review and adjudicate multi-account detection flags.",
    BANG_REGENERATE: "Trigger galaxy generation (bang) regeneration runs.",
    SCOPES_GRANT: "Grant admin scopes to other users.",
    SCOPES_REVOKE: "Revoke admin scopes from other users.",
    AUDIT_VIEW: "View the AdminActionLog audit trail (admin actions).",
    GALAXY_MANAGE: (
        "Structural edits to galaxy sectors, ports, planets, and warp links "
        "(not bang regeneration; use admin.bang.regenerate for that)."
    ),
    PLAYERS_ADJUST_CREDITS: "Set, grant, or deduct player credit balances.",
    SHIPS_MANAGE: "Create, edit, delete, or teleport ships.",
    COMBAT_INTERVENE: "Intervene in active combat encounters.",
    ECONOMY_INTERVENE: "Intervene in economy and market operations.",
    SECURITY_ACT: "Take security enforcement actions (e.g., blocks, alerts).",
    DISPUTES_RESOLVE: "Resolve contract disputes and escrow outcomes.",
}

assert set(SCOPE_DESCRIPTIONS.keys()) == ALL_SCOPES
assert all(SCOPE_DESCRIPTIONS[s].strip() for s in ALL_SCOPES)
