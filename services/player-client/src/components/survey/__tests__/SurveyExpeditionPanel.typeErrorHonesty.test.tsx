// @vitest-environment jsdom
/**
 * LEG-3154 Soft-ORDER — SurveyExpeditionPanel TypeError densify + list restore honesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SurveyExpeditionPanel, { formatSurveyExpeditionError } from '../SurveyExpeditionPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../OrbitalScanView', () => ({
  default: () => <div data-testid="orbital-scan" />,
}));
vi.mock('../SiteGridPreview', () => ({
  default: () => <div data-testid="site-grid" />,
}));

const { mockList, mockLaunch } = vi.hoisted(() => ({
  mockList: vi.fn(),
  mockLaunch: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  expeditionAPI: {
    list: (...a: unknown[]) => mockList(...a),
    getStatus: vi.fn(),
    launch: (...a: unknown[]) => mockLaunch(...a),
    reroll: vi.fn(),
    settle: vi.fn(),
  },
}));

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('formatSurveyExpeditionError TypeError densify (LEG-3154)', () => {
  it('returns fallback on TypeError network collapse for dispatch', () => {
    const text = formatSurveyExpeditionError(new TypeError('Failed to fetch'), 'Failed to launch expedition');
    expect(text).toBe('Failed to launch expedition');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('returns fallback on TypeError for settle path', () => {
    const text = formatSurveyExpeditionError(
      new TypeError('Failed to fetch'),
      'Another expedition settled this site first.',
    );
    expect(text).toBe('Another expedition settled this site first.');
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('preserves gameserver Error.message detail', () => {
    expect(formatSurveyExpeditionError(new Error('expedition_denied'), 'fallback')).toBe(
      'expedition_denied',
    );
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError (LEG-3288)', () => {
    const fallback = 'Failed to launch expedition';
    expect(formatSurveyExpeditionError(new Error('Network Error'), fallback)).toBe(fallback);
    expect(formatSurveyExpeditionError(new Error('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatSurveyExpeditionError(new Error(''), fallback)).toBe(fallback);
  });
});

describe('SurveyExpeditionPanel integration TypeError densify (LEG-3154)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockList.mockReset();
    mockLaunch.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('list restore failure shows honest error instead of silent empty state', async () => {
    mockList.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<SurveyExpeditionPanel planetId="planet-1" planetName="Test" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const listErr = container.querySelector('[data-testid="survey-list-error"]');
    expect(listErr?.textContent).toMatch(/Could not restore your expedition status/i);
    expect(listErr?.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.querySelector('[data-testid="survey-no-expedition"]')).toBeNull();
  });

  it('launch TypeError surfaces fallback without Failed to fetch', async () => {
    mockList.mockResolvedValue({ expeditions: [] });
    mockLaunch.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<SurveyExpeditionPanel planetId="planet-1" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const launchBtn = container.querySelector('.survey-btn-primary') as HTMLButtonElement;
    await act(async () => {
      launchBtn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(mockLaunch).toHaveBeenCalled();
    });

    const alert = container.querySelector('.survey-error');
    expect(alert?.textContent).toMatch(/Failed to launch expedition/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
  });
});


describe('formatSurveyExpeditionError 403/429 densify (LEG-4090)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    const fallback = 'Failed to launch expedition';
    expect(formatSurveyExpeditionError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatSurveyExpeditionError(apiRequestError(403, 'expedition_denied'), fallback)).toBe(
      'expedition_denied',
    );
    expect(formatSurveyExpeditionError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatSurveyExpeditionError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatSurveyExpeditionError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});
