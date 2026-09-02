// @vitest-environment jsdom
/**
 * LEG-4076 Soft-ORDER — ResearchPanel 403/429 densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import { formatResearchPanelError } from '../ResearchPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatResearchPanelError 403/429 densify (LEG-4076)', () => {
  it('maps 403/429 without raw transport leakage', () => {
    expect(formatResearchPanelError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatResearchPanelError(apiRequestError(403, 'research_view_denied'))).toBe(
      'research_view_denied',
    );
    expect(formatResearchPanelError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatResearchPanelError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatResearchPanelError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
