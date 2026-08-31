// @vitest-environment jsdom
/**
 * LEG-3070 Soft-ORDER — formatQuantumDriveApiError TypeError densify.
 * Echo scan / jump / refine must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import { formatQuantumDriveApiError } from '../QuantumDriveConsole';

const SCAN_FALLBACK = 'Echo scan failed — drive sensors unresponsive';
const JUMP_FALLBACK = 'Quantum jump failed — drive aborted the translation';

describe('formatQuantumDriveApiError (LEG-3070)', () => {
  it('falls back on TypeError network collapse for echo scan', () => {
    const text = formatQuantumDriveApiError(new TypeError('Failed to fetch'), SCAN_FALLBACK);
    expect(text).toMatch(/Echo scan failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on TypeError network collapse for quantum jump', () => {
    const text = formatQuantumDriveApiError(new TypeError('Failed to fetch'), JUMP_FALLBACK);
    expect(text).toMatch(/Quantum jump failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves gameserver axios detail when present', () => {
    const err = {
      response: { data: { detail: 'Insufficient turns for echo scan' } },
    };
    expect(formatQuantumDriveApiError(err, SCAN_FALLBACK)).toBe(
      'Insufficient turns for echo scan',
    );
  });
});
