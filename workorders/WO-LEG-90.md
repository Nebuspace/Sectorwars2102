# WO-LEG-90 — GET /medals/me exposes pinned_medal_id

## Goal
Additive `pinned_medal_id` on `PlayerMedalsResponse` / GET `/api/v1/medals/me` so Trophy Room can hydrate the server pin on first paint (LEG-87 residual).

## Scope
- `services/gameserver/src/api/routes/medals.py` — field + assemble via `public_medal_identity` (same source as PUT `/me/pin`)
- `services/gameserver/tests/unit/test_medal_pin_leg59.py` — pin then getMe / clear then null

## Out of scope
Admin bulk; PlayerNamePlate; client hydrate (LEG-91).

## Stack
Branch `wo/LEG-90` from `wo/LEG-59` (PR #605) — pin write is not on feat tip yet.

## Accept
- GET /me returns current pin from `Player.settings.medal_privacy.pinned_medal_id`
- Focused pytest green
