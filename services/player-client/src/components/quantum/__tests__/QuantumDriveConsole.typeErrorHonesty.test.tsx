// @vitest-environment jsdom
/**
 * LEG-3070 Soft-ORDER — formatQuantumDriveApiError TypeError densify.
 * LEG-3394 Soft-ORDER — axios Network Error / Failed to fetch densify.
 * Echo scan / jump / refine must not surface raw Failed to fetch / TypeError /
 * Network Error.
 */
import { describe, it, expect } from 'vitest';
import { formatQuantumDriveApiError } from '../QuantumDriveConsole';

const SCAN_FALLBACK = 'Echo scan failed — drive sensors unresponsive';
const JUMP_FALLBACK = 'Quantum jump failed — drive aborted the translation';
const REFINE_FALLBACK = 'Charge refinement failed';


const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};
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

describe('formatQuantumDriveApiError Network Error densify (LEG-3394)', () => {
  it('falls back on axios Network Error / Failed to fetch / whitespace', () => {
    expect(formatQuantumDriveApiError(new Error('Network Error'), SCAN_FALLBACK)).toBe(
      SCAN_FALLBACK,
    );
    expect(formatQuantumDriveApiError(new Error('Failed to fetch'), JUMP_FALLBACK)).toBe(
      JUMP_FALLBACK,
    );
    expect(formatQuantumDriveApiError(new Error('   '), REFINE_FALLBACK)).toBe(REFINE_FALLBACK);
    expect(formatQuantumDriveApiError(new Error('Network Error'), SCAN_FALLBACK)).not.toMatch(
      /Network Error/i,
    );
    expect(formatQuantumDriveApiError({ message: 'Network Error' }, SCAN_FALLBACK)).toBe(
      SCAN_FALLBACK,
    );
  });

  it('preserves GS detail when present alongside non-collapse message', () => {
    const err = {
      message: 'Request failed with status code 400',
      response: { data: { detail: 'Drive lattice desync — refine charge first' } },
    };
    expect(formatQuantumDriveApiError(err, REFINE_FALLBACK)).toBe(
      'Drive lattice desync — refine charge first',
    );
  });
});

describe('formatQuantumDriveApiError 403/429 densify (LEG-4037)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatQuantumDriveApiError(apiRequestError(403), SCAN_FALLBACK)).toMatch(/permission/i);
    expect(formatQuantumDriveApiError(apiRequestError(403, 'drive_denied'), SCAN_FALLBACK)).toBe(
      'drive_denied',
    );
    expect(formatQuantumDriveApiError(apiRequestError(429), JUMP_FALLBACK)).toMatch(/rate limit/i);
    expect(formatQuantumDriveApiError(apiRequestError(429), JUMP_FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatQuantumDriveApiError(apiRequestError(403), SCAN_FALLBACK)).not.toMatch(/TypeError/i);
  });
});
