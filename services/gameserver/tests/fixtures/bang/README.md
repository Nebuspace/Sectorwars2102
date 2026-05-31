# Bang Universe JSON Fixtures

Captured deterministic outputs from the `sw2102-bang` CLI for use by the
`BangImportService` unit tests
(`services/gameserver/tests/unit/test_bang_import_service.py`).

## Naming convention

```
v{bang_major}_{minor}_{patch}_{region_type}[_{size_qualifier}].json
```

For v1.3.0:
- `v1_3_0_player_owned_small.json` — 1000-sector player_owned region
- `v1_3_0_terran_space.json` — canonical 300-sector terran_space region
- `v1_3_0_central_nexus.json` — canonical 5000-sector central_nexus region

## Regeneration

These fixtures are checked in so unit tests do not require a bang
checkout. To regenerate after a bang version bump (e.g. v1.4.0):

```bash
cd /Users/mrathbone/github/Nebuspace/sw2102-bang
# ensure dist/ is up to date — if not, run:  npm install && npm run build
node dist/cli.js --seed 42 --sectors 1000 --region-type player_owned --json-out \
    > ../Sectorwars2102/services/gameserver/tests/fixtures/bang/v1_4_0_player_owned_small.json
node dist/cli.js --seed 42 --sectors 300  --region-type terran_space    --json-out \
    > ../Sectorwars2102/services/gameserver/tests/fixtures/bang/v1_4_0_terran_space.json
node dist/cli.js --seed 42 --sectors 5000 --region-type central_nexus   --json-out \
    > ../Sectorwars2102/services/gameserver/tests/fixtures/bang/v1_4_0_central_nexus.json
```

Then update the constants at the top of `test_bang_import_service.py` to
point at the new file names, and adjust any assertions that pin specific
values (e.g. cluster count, formation count).

The seed `42` is intentional: it produces a reproducible Universe so
fixture regeneration is deterministic.

## Why bang itself, not Docker?

These fixtures must be runnable locally without Docker (per Max's
Mac-CPU-throttling rule); bang is a single-file Node CLI so running it
with `node dist/cli.js` is the lightest path. Docker is used at runtime
in the gameserver (via `BangImportService.invoke_bang`), but for fixture
capture the local Node binary is sufficient.
