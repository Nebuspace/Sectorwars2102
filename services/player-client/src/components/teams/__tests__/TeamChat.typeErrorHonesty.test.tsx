// @vitest-environment jsdom
/**
 * LEG-3412 Soft-ORDER — TeamChat Network Error densify.
 */
import { describe, it, expect } from 'vitest';
import { formatTeamChatSendError } from '../TeamChat';

describe('TeamChat TypeError densify (LEG-3412)', () => {
  it('formatTeamChatSendError falls back on TypeError network collapse', () => {
    const text = formatTeamChatSendError(new TypeError('Failed to fetch'));
    expect(text).toBe('Failed to send message.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatTeamChatSendError(new Error('Network Error'))).toBe('Failed to send message.');
    expect(formatTeamChatSendError(new Error('Failed to fetch'))).toBe('Failed to send message.');
    expect(formatTeamChatSendError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatTeamChatSendError(new Error('muted'))).toBe('muted');
  });
});
