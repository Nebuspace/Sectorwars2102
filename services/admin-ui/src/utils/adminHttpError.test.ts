import { describe, it, expect } from 'vitest';
import { adminHttpErrorMessage, adminHttpStatus } from './adminHttpError';

describe('adminHttpError', () => {
  it('maps 403 to scope denial when scope label provided', () => {
    const err = Object.assign(new Error('HTTP 403'), { response: { status: 403 } });
    expect(adminHttpStatus(err)).toBe(403);
    expect(adminHttpErrorMessage(err, 'Failed', 'PLAYERS_VIEW')).toMatch(
      /PLAYERS_VIEW/,
    );
  });

  it('maps 429 to admin rate-limit copy', () => {
    const err = Object.assign(new Error('HTTP 429'), { response: { status: 429 } });
    expect(adminHttpErrorMessage(err, 'Failed', 'PLAYERS_VIEW')).toMatch(
      /rate limit/i,
    );
  });

  it('keeps fallback for non-HTTP errors', () => {
    expect(adminHttpErrorMessage(new Error('network'), 'Failed to load')).toBe(
      'Failed to load',
    );
  });
});
