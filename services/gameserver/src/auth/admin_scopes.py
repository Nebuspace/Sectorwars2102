"""Admin scope catalog — RBAC Phase A1 (ADR-0058 A-F2) + Max-ruled expansion.

ADR-0058's 19 platform scopes are VERBATIM.  Operational scopes (e.g.
``admin.disputes.resolve``) are Max-ruled additions (19→26); remaining 6
land with the re-map + seed migration.  GRANTS live as AdminScopeGrant
rows; the catalog is the fixed vocabulary they reference.

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
# audit/design-briefs/rbac-scope-expansion-2026-07-17.md). Staged here for #4
# cutover; the other 6 land with the re-map + seed migration.
# Grantable today via SCOPES_GRANT API; seed migration follows (serialized vs BULK).
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
