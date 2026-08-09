// @vitest-environment jsdom
/**
 * inputValidation — OWASP-guideline client-side XSS sanitization,
 * validation, rate-limiting, and audit-logging utilities. Pure/localStorage
 * -backed logic; no auth/session/credential handling. localStorage is real
 * (jsdom) and cleared each test since several methods persist to it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { InputValidator, SecurityAudit, ValidationRules } from '../inputValidation';

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('ValidationRules', () => {
  it.each([
    ['PLAYER_NAME', 'abc_123', true],
    ['PLAYER_NAME', 'ab', false], // below min length 3
    ['PLAYER_NAME', 'a'.repeat(21), false], // above max length 20
    ['PLAYER_NAME', 'bad name!', false], // disallowed char
    ['SHIP_NAME', 'My Ship-1', true],
    ['SHIP_NAME', '', false], // below min length 1
    ['TEAM_NAME', 'Team_A', true],
    ['TEAM_NAME', 'ab', false], // below min length 3
    ['POSITIVE_INTEGER', '42', true],
    ['POSITIVE_INTEGER', '-1', false],
    ['PERCENTAGE', '100', true],
    ['PERCENTAGE', '0', true],
    ['PERCENTAGE', '101', false],
    ['TARGET_TYPE', 'ship', true],
    ['TARGET_TYPE', 'asteroid', false],
    ['COMBAT_ACTION', 'fire', true],
    ['COMBAT_ACTION', 'surrender', false],
    ['MESSAGE_CONTENT', 'Hello, world!', true],
    ['MESSAGE_CONTENT', 'a'.repeat(501), false], // above max length 500
  ] as const)('%s.test(%j) === %s', (rule, input, expected) => {
    expect(ValidationRules[rule].test(input)).toBe(expected);
  });

  it('UUID matches a canonical UUID case-insensitively', () => {
    expect(ValidationRules.UUID.test('550e8400-e29b-41d4-a716-446655440000')).toBe(true);
    expect(ValidationRules.UUID.test('550E8400-E29B-41D4-A716-446655440000')).toBe(true);
    expect(ValidationRules.UUID.test('not-a-uuid')).toBe(false);
  });

  it('NUMERIC_ID matches digits only', () => {
    expect(ValidationRules.NUMERIC_ID.test('1001')).toBe(true);
    expect(ValidationRules.NUMERIC_ID.test('10a1')).toBe(false);
  });
});

describe('InputValidator.sanitizeText', () => {
  it('returns an empty string for non-string input', () => {
    expect(InputValidator.sanitizeText(null as unknown as string)).toBe('');
    expect(InputValidator.sanitizeText(undefined as unknown as string)).toBe('');
    expect(InputValidator.sanitizeText(42 as unknown as string)).toBe('');
  });

  it('trims whitespace and caps length at 1000 characters', () => {
    expect(InputValidator.sanitizeText('  hi  ')).toBe('hi');
    expect(InputValidator.sanitizeText('a'.repeat(2000)).length).toBe(1000);
  });

  it('strips script tags AND their inner content entirely (DOMPurify default)', () => {
    expect(InputValidator.sanitizeText('<script>alert(1)</script>hello')).toBe('hello');
  });

  it('strips a real HTML tag (and just the tag, not surrounding text)', () => {
    expect(InputValidator.sanitizeText('a <b> c')).toBe('a  c');
  });

  it('HTML-entity-encodes (does not strip) angle brackets that are not a recognized tag', () => {
    // DOMPurify escapes non-tag "<"/">" to &lt;/&gt; rather than dropping
    // them; the code's own `[<>]` strip only catches literal characters, so
    // entity-encoded brackets survive sanitizeText untouched.
    expect(InputValidator.sanitizeText('1 < 5 and 6 > 3')).toBe('1 &lt; 5 and 6 &gt; 3');
  });

  it('strips javascript:/data:/vbscript: URL schemes case-insensitively', () => {
    expect(InputValidator.sanitizeText('javascript:alert(1)')).toBe('alert(1)');
    expect(InputValidator.sanitizeText('JAVASCRIPT:alert(1)')).toBe('alert(1)');
    expect(InputValidator.sanitizeText('data:text/html,x')).toBe('text/html,x');
    expect(InputValidator.sanitizeText('vbscript:msgbox(1)')).toBe('msgbox(1)');
  });

  it('iteratively strips nested on*= event handlers exposed by a prior pass', () => {
    // Removing the outer "onload=" would naively expose a second "onerror="
    // sitting right behind it — the while-loop must keep going until stable.
    expect(InputValidator.sanitizeText('onloadonerror=x')).toBe('x');
  });

  it('leaves ordinary safe text untouched (beyond trim)', () => {
    expect(InputValidator.sanitizeText('Hello, trader! 100 credits.')).toBe('Hello, trader! 100 credits.');
  });
});

describe('InputValidator.validatePlayerInput', () => {
  it('returns false for non-string input', () => {
    expect(InputValidator.validatePlayerInput(42 as unknown as string, 'PLAYER_NAME')).toBe(false);
  });

  it('delegates to the named ValidationRules regex', () => {
    expect(InputValidator.validatePlayerInput('valid_name', 'PLAYER_NAME')).toBe(true);
    expect(InputValidator.validatePlayerInput('!!', 'PLAYER_NAME')).toBe(false);
  });

  it('returns false and logs an error for an unknown rule type', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(
      InputValidator.validatePlayerInput('x', 'NOT_A_RULE' as keyof typeof ValidationRules)
    ).toBe(false);
    expect(spy).toHaveBeenCalled();
  });
});

describe('InputValidator.validateNumeric', () => {
  it('accepts a string within the default range', () => {
    expect(InputValidator.validateNumeric('42')).toEqual({ valid: true, value: 42 });
  });

  it('accepts a number within the default range', () => {
    expect(InputValidator.validateNumeric(42)).toEqual({ valid: true, value: 42 });
  });

  it('rejects non-numeric strings', () => {
    expect(InputValidator.validateNumeric('abc')).toEqual({ valid: false });
  });

  it('rejects a value below the given min', () => {
    expect(InputValidator.validateNumeric(5, 10, 100)).toEqual({ valid: false });
  });

  it('rejects a value above the given max', () => {
    expect(InputValidator.validateNumeric(500, 0, 100)).toEqual({ valid: false });
  });

  it('accepts the exact min and max boundaries', () => {
    expect(InputValidator.validateNumeric(0, 0, 100)).toEqual({ valid: true, value: 0 });
    expect(InputValidator.validateNumeric(100, 0, 100)).toEqual({ valid: true, value: 100 });
  });
});

describe('InputValidator.validateCombatParams', () => {
  it('passes with no params at all (nothing to validate)', () => {
    expect(InputValidator.validateCombatParams({})).toEqual({ valid: true, errors: [] });
  });

  it('rejects an invalid targetType', () => {
    const result = InputValidator.validateCombatParams({ targetType: 'asteroid' });
    expect(result.valid).toBe(false);
    expect(result.errors).toContain('Invalid target type');
  });

  it('accepts a valid targetType', () => {
    expect(InputValidator.validateCombatParams({ targetType: 'ship' }).valid).toBe(true);
  });

  it('accepts a UUID targetId', () => {
    expect(
      InputValidator.validateCombatParams({ targetId: '550e8400-e29b-41d4-a716-446655440000' }).valid
    ).toBe(true);
  });

  it('accepts a numeric targetId', () => {
    expect(InputValidator.validateCombatParams({ targetId: '1001' }).valid).toBe(true);
  });

  it('rejects a malformed targetId', () => {
    const result = InputValidator.validateCombatParams({ targetId: 'ship-1' });
    expect(result.valid).toBe(false);
    expect(result.errors).toContain('Invalid target ID format');
  });

  it('rejects an out-of-range droneCount', () => {
    const result = InputValidator.validateCombatParams({ droneCount: 10000 });
    expect(result.valid).toBe(false);
    expect(result.errors).toContain('Invalid drone count');
  });

  it('accepts droneCount at the boundary', () => {
    expect(InputValidator.validateCombatParams({ droneCount: 9999 }).valid).toBe(true);
    expect(InputValidator.validateCombatParams({ droneCount: 0 }).valid).toBe(true);
  });

  it('accumulates multiple errors at once', () => {
    const result = InputValidator.validateCombatParams({
      targetType: 'asteroid',
      targetId: 'bad-id',
      droneCount: -1,
    });
    expect(result.valid).toBe(false);
    expect(result.errors).toHaveLength(3);
  });
});

describe('InputValidator.sanitizeMessage', () => {
  it('sanitizes, caps length at 500, and collapses whitespace', () => {
    expect(InputValidator.sanitizeMessage('hello   \n\n  world')).toBe('hello world');
  });

  it('strips XSS content like sanitizeText', () => {
    expect(InputValidator.sanitizeMessage('<script>x</script>hi')).toBe('hi');
  });
});

describe('InputValidator.validateShipName', () => {
  it('returns valid + sanitized for an acceptable name', () => {
    expect(InputValidator.validateShipName('Star Runner')).toEqual({
      valid: true,
      sanitized: 'Star Runner',
    });
  });

  it('returns invalid for a name failing SHIP_NAME after sanitization', () => {
    // sanitizeText strips the angle brackets, leaving "script" — 30-char cap
    // still applies; use a name that stays invalid after stripping (too long).
    expect(InputValidator.validateShipName('x'.repeat(31)).valid).toBe(false);
  });
});

describe('InputValidator.sanitizeSearchQuery', () => {
  it('caps length at 100 and strips non-word/space/hyphen characters', () => {
    expect(InputValidator.sanitizeSearchQuery('cargo-hauler!!')).toBe('cargo-hauler');
  });

  it('strips XSS content like sanitizeText', () => {
    expect(InputValidator.sanitizeSearchQuery('<script>x</script>')).toBe('');
  });
});

describe('InputValidator.checkRateLimit / clearRateLimit', () => {
  it('allows attempts under the max within the window', () => {
    expect(InputValidator.checkRateLimit('action-a', 3, 60000)).toBe(true);
    expect(InputValidator.checkRateLimit('action-a', 3, 60000)).toBe(true);
    expect(InputValidator.checkRateLimit('action-a', 3, 60000)).toBe(true);
  });

  it('blocks once maxAttempts is reached within the window', () => {
    InputValidator.checkRateLimit('action-b', 2, 60000);
    InputValidator.checkRateLimit('action-b', 2, 60000);
    expect(InputValidator.checkRateLimit('action-b', 2, 60000)).toBe(false);
  });

  it('tracks distinct actionKeys independently', () => {
    InputValidator.checkRateLimit('action-c1', 1, 60000);
    expect(InputValidator.checkRateLimit('action-c1', 1, 60000)).toBe(false);
    expect(InputValidator.checkRateLimit('action-c2', 1, 60000)).toBe(true);
  });

  it('expires old attempts outside the window, allowing again', () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    InputValidator.checkRateLimit('action-d', 1, 1000);
    expect(InputValidator.checkRateLimit('action-d', 1, 1000)).toBe(false);

    vi.setSystemTime(1500); // past the 1000ms window
    expect(InputValidator.checkRateLimit('action-d', 1, 1000)).toBe(true);
  });

  it('allows the action (fail-open) when localStorage throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('disabled');
    });
    expect(InputValidator.checkRateLimit('action-e')).toBe(true);
    spy.mockRestore();
  });

  it('clearRateLimit removes the stored attempts, resetting the window', () => {
    InputValidator.checkRateLimit('action-f', 1, 60000);
    expect(InputValidator.checkRateLimit('action-f', 1, 60000)).toBe(false);

    InputValidator.clearRateLimit('action-f');
    expect(InputValidator.checkRateLimit('action-f', 1, 60000)).toBe(true);
  });

  it('clearRateLimit does not throw when localStorage.removeItem throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('disabled');
    });
    expect(() => InputValidator.clearRateLimit('action-g')).not.toThrow();
    spy.mockRestore();
  });
});

describe('SecurityAudit.log', () => {
  it('appends an event with a timestamp to the security_audit localStorage log', () => {
    SecurityAudit.log({ type: 'validation_failure', details: { field: 'name' } });
    const log = JSON.parse(localStorage.getItem('security_audit')!);
    expect(log).toHaveLength(1);
    expect(log[0]).toMatchObject({ type: 'validation_failure', details: { field: 'name' } });
    expect(typeof log[0].timestamp).toBe('string');
  });

  it('caps the stored log at the most recent 100 entries', () => {
    for (let i = 0; i < 105; i++) {
      SecurityAudit.log({ type: 'rate_limit_exceeded', details: { i } });
    }
    const log = JSON.parse(localStorage.getItem('security_audit')!);
    expect(log).toHaveLength(100);
    expect(log[0].details.i).toBe(5); // oldest 5 trimmed off
    expect(log[99].details.i).toBe(104);
  });

  it('does not throw when localStorage.setItem throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });
    expect(() => SecurityAudit.log({ type: 'xss_attempt', details: {} })).not.toThrow();
    spy.mockRestore();
  });
});
