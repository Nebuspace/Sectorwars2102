// @vitest-environment jsdom
/**
 * LEG-785 — AssistanceLevelSettings Vitest (hydrate / persist / reject medium).
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockGetTradingProfile = vi.fn();
const mockUpdateAIPreferences = vi.fn();

vi.mock('../../../services/aiTradingService', () => ({
  aiTradingService: {
    getTradingProfile: (...a: unknown[]) => mockGetTradingProfile(...a),
    updateAIPreferences: (...a: unknown[]) => mockUpdateAIPreferences(...a),
  },
}));

import AssistanceLevelSettings, {
  coerceAssistanceLevel,
  formatAssistanceLevelError,
} from '../AssistanceLevelSettings';
import { AI_ASSISTANCE_LEVELS } from '../../ai/types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sampleProfile = {
  player_id: 'p-1',
  risk_tolerance: 0.42,
  ai_assistance_level: 'full' as const,
  average_profit_per_trade: 0,
  total_trades_analyzed: 0,
};

describe('AssistanceLevelSettings', () => {
  let container: HTMLElement;
  let root: Root;

  beforeEach(() => {
    mockGetTradingProfile.mockReset();
    mockUpdateAIPreferences.mockReset();
    mockGetTradingProfile.mockResolvedValue(sampleProfile);
    mockUpdateAIPreferences.mockResolvedValue(undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('exposes the canonical 4-value set and rejects medium', () => {
    expect([...AI_ASSISTANCE_LEVELS]).toEqual(['minimal', 'quiet', 'standard', 'full']);
    expect(AI_ASSISTANCE_LEVELS).not.toContain('medium');
    expect(coerceAssistanceLevel('medium')).toBe('standard');
    expect(coerceAssistanceLevel('quiet')).toBe('quiet');
  });

  it('hydrates the selector from GET /profile', async () => {
    await act(async () => {
      root.render(<AssistanceLevelSettings />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const select = container.querySelector('#aria-assistance-level') as HTMLSelectElement;
    expect(select).toBeTruthy();
    expect(select.value).toBe('full');
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(['minimal', 'quiet', 'standard', 'full']);
    expect(values).not.toContain('medium');
    expect(mockGetTradingProfile).toHaveBeenCalled();
  });

  it('persists quiet via mocked PUT with existing risk_tolerance', async () => {
    await act(async () => {
      root.render(<AssistanceLevelSettings />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const select = container.querySelector('#aria-assistance-level') as HTMLSelectElement;
    await act(async () => {
      select.value = 'quiet';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(mockUpdateAIPreferences).toHaveBeenCalledWith({
      ai_assistance_level: 'quiet',
      risk_tolerance: 0.42,
    });
    expect(select.value).toBe('quiet');
  });

  it('does not PUT medium and surfaces a rejection', async () => {
    await act(async () => {
      root.render(<AssistanceLevelSettings />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const select = container.querySelector('#aria-assistance-level') as HTMLSelectElement;
    await act(async () => {
      Object.defineProperty(select, 'value', { writable: true, value: 'medium' });
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(mockUpdateAIPreferences).not.toHaveBeenCalled();
    expect(container.textContent).toMatch(/minimal, quiet, standard, or full/i);
  });

  it('surfaces PUT failure text', async () => {
    mockUpdateAIPreferences.mockRejectedValueOnce(new Error('Failed to update AI preferences: Forbidden'));

    await act(async () => {
      root.render(<AssistanceLevelSettings />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const select = container.querySelector('#aria-assistance-level') as HTMLSelectElement;
    await act(async () => {
      select.value = 'minimal';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Failed to update AI preferences: Forbidden');
  });

  it('surfaces GET failure text', async () => {
    mockGetTradingProfile.mockRejectedValueOnce(new Error('Failed to fetch trading profile: Unauthorized'));

    await act(async () => {
      root.render(<AssistanceLevelSettings />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Failed to fetch trading profile: Unauthorized');
  });

  it('surfaces load 500 server detail via formatAssistanceLevelError', async () => {
    const err = new Error('Internal server error: profile unavailable');
    (err as { status?: number }).status = 500;
    mockGetTradingProfile.mockRejectedValueOnce(err);

    await act(async () => {
      root.render(<AssistanceLevelSettings />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Internal server error: profile unavailable');
    expect(formatAssistanceLevelError(err, 'load')).toBe(
      'Internal server error: profile unavailable',
    );
  });

  it('surfaces update validation refusal with server detail', async () => {
    mockUpdateAIPreferences.mockRejectedValueOnce(
      Object.assign(new Error('Invalid assistance level value'), { status: 400 }),
    );

    await act(async () => {
      root.render(<AssistanceLevelSettings />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    const select = container.querySelector('#aria-assistance-level') as HTMLSelectElement;
    await act(async () => {
      select.value = 'minimal';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Invalid assistance level value');
  });
});
