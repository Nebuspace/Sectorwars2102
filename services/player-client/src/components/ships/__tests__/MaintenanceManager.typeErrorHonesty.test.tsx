// @vitest-environment jsdom
/**
 * LEG-3472 Soft-ORDER — MaintenanceManager Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import {
  formatMaintenanceLoadError,
  formatMaintenanceRepairError,
} from '../MaintenanceManager';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('MaintenanceManager TypeError densify (LEG-3472)', () => {
  it('formatMaintenanceLoadError falls back on TypeError network collapse', () => {
    const text = formatMaintenanceLoadError(new TypeError('Failed to fetch'));
    expect(text).toBe('Maintenance data is unavailable.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatMaintenanceRepairError falls back on TypeError network collapse', () => {
    const text = formatMaintenanceRepairError(new TypeError('Failed to fetch'));
    expect(text).toBe('Servicing failed.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatMaintenanceLoadError(new Error('Network Error'))).toBe(
      'Maintenance data is unavailable.',
    );
    expect(formatMaintenanceLoadError(new Error('Failed to fetch'))).toBe(
      'Maintenance data is unavailable.',
    );
    expect(formatMaintenanceRepairError(new Error('Network Error'))).toBe('Servicing failed.');
    expect(formatMaintenanceRepairError(new Error('Failed to fetch'))).toBe('Servicing failed.');
    expect(formatMaintenanceLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatMaintenanceLoadError(new Error('yard_offline'))).toBe('yard_offline');
    expect(formatMaintenanceRepairError(new Error('repair_denied'))).toBe('repair_denied');
  });
});

describe('MaintenanceManager 403/429 densify (LEG-3981)', () => {
  it('formatMaintenanceLoadError surfaces 403/429 without raw status codes', () => {
    expect(formatMaintenanceLoadError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatMaintenanceLoadError(apiRequestError(403, 'maintenance_view_denied'))).toBe(
      'maintenance_view_denied',
    );
    expect(formatMaintenanceLoadError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatMaintenanceLoadError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatMaintenanceLoadError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });

  it('formatMaintenanceRepairError surfaces 403/429 without raw status codes', () => {
    expect(formatMaintenanceRepairError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatMaintenanceRepairError(apiRequestError(403, 'repair_denied'))).toBe('repair_denied');
    expect(formatMaintenanceRepairError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatMaintenanceRepairError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatMaintenanceRepairError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
