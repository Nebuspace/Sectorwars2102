import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, 'FleetManagement.tsx'), 'utf8');

/** Tip ShipType player-facing values (exclude NPC_*). */
const TIP_PLAYER_SHIP_TYPES = [
  'ESCAPE_POD',
  'LIGHT_FREIGHTER',
  'CARGO_HAULER',
  'FAST_COURIER',
  'CITIZEN_CLIPPER',
  'SCOUT_SHIP',
  'COLONY_SHIP',
  'DEFENDER',
  'CARRIER',
  'WARP_JUMPER',
];

describe('FleetManagement Soft-ORDER SHIP_TYPES (LEG-1463)', () => {
  it('SHIP_TYPES array matches tip player-facing ShipType enum', () => {
    const match = src.match(/const SHIP_TYPES = \[([\s\S]*?)\];/);
    expect(match).toBeTruthy();
    const body = match![1];
    for (const t of TIP_PLAYER_SHIP_TYPES) {
      expect(body).toContain(`'${t}'`);
    }
    for (const legacy of [
      'BATTLESHIP',
      'CRUISER',
      'DESTROYER',
      'FIGHTER',
      'MEDIUM_FREIGHTER',
      'HEAVY_FREIGHTER',
      'NPC_MARSHAL_INTERDICTOR',
      'NPC_SENTINEL_INTERDICTOR',
    ]) {
      expect(body).not.toContain(`'${legacy}'`);
    }
  });
});

describe('FleetManagement Soft-ORDER teleport query (LEG-1470)', () => {
  it('posts teleport with target_sector_id as query params, not JSON body', () => {
    expect(src).toMatch(
      /api\.post\(\s*`\/api\/v1\/admin\/ships\/\$\{selectedShip\.id\}\/teleport`\s*,\s*null\s*,\s*\{\s*params:\s*\{\s*target_sector_id:/,
    );
    expect(src).not.toMatch(
      /api\.post\(`\/api\/v1\/admin\/ships\/\$\{selectedShip\.id\}\/teleport`,\s*\{\s*target_sector_id:/,
    );
  });
});
