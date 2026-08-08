// @vitest-environment node
/**
 * WO-WIRE-LANDER-TRACTOR-CATALOG-UNLOCK — lander+tractor catalog flags live.
 * Mining stays deferred (server consumer_inert). Source-level pin so a
 * re-deferred regression fails CI without mounting ModuleGridInterface.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

const SRC = readFileSync(
  resolve(__dirname, '../ModuleGridInterface.tsx'),
  'utf8'
);

describe('ModuleGridInterface catalog unlock (lander/tractor)', () => {
  it('lander is not deferred', () => {
    expect(SRC).toMatch(/cls:\s*'lander'[\s\S]*?baseCost:\s*20000(?![\s\S]*?deferred:\s*true)/);
    expect(SRC).not.toMatch(/cls:\s*'lander'[^}]*deferred:\s*true/);
  });

  it('tractor is not deferred', () => {
    expect(SRC).not.toMatch(/cls:\s*'tractor'[^}]*deferred:\s*true/);
  });

  it('mining remains deferred', () => {
    expect(SRC).toMatch(/cls:\s*'mining'[^}]*deferred:\s*true/);
  });
});
