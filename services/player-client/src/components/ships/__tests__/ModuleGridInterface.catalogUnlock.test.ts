// @vitest-environment node
/**
 * WO-WIRE-LANDER-TRACTOR-CATALOG-UNLOCK — lander+tractor catalog flags live.
 * Mining stays deferred (server consumer_inert). Source-level pin.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

const SRC = readFileSync(
  resolve(__dirname, '../ModuleGridInterface.tsx'),
  'utf8'
);

function extractFamily(cls: string): string {
  const marker = `{ cls: '${cls}'`;
  const i = SRC.indexOf(marker);
  expect(i).toBeGreaterThanOrEqual(0);
  const j = SRC.indexOf('},', i);
  expect(j).toBeGreaterThan(i);
  return SRC.slice(i, j + 2);
}

describe('ModuleGridInterface catalog unlock (lander/tractor)', () => {
  it('lander is not deferred', () => {
    expect(extractFamily('lander')).not.toMatch(/deferred:\s*true/);
  });

  it('tractor is not deferred', () => {
    expect(extractFamily('tractor')).not.toMatch(/deferred:\s*true/);
  });

  it('mining remains deferred', () => {
    expect(extractFamily('mining')).toMatch(/deferred:\s*true/);
  });
});
