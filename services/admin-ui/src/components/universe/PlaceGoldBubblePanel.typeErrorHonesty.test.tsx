import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PlaceGoldBubblePanel, {
  formatPlaceGoldBubbleError,
} from './PlaceGoldBubblePanel';
import {
  GOLD_BUBBLE_INTERIOR_SIZE_MIN,
  placeGoldBubble,
} from '../../services/placeGoldBubbleApi';

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

vi.mock('../../services/placeGoldBubbleApi', async () => {
  const actual = await vi.importActual<
    typeof import('../../services/placeGoldBubbleApi')
  >('../../services/placeGoldBubbleApi');
  return {
    ...actual,
    placeGoldBubble: vi.fn(),
  };
});

function makeInteriorIds(count: number): string[] {
  return Array.from({ length: count }, (_, i) => {
    const n = (i + 1).toString(16).padStart(12, '0');
    return `cccccccc-cccc-4ccc-8ccc-${n}`;
  });
}

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3618 Soft-ORDER — PlaceGoldBubblePanel TypeError/Network Error honesty densify.
 * LEG-3915 Soft-ORDER — HTTP 429 densify (invent=0).
 */
describe('PlaceGoldBubblePanel typeErrorHonesty densify (LEG-3618 / LEG-3915)', () => {
  const regionId = '11111111-1111-4111-8111-111111111111';
  const gateway = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

  beforeEach(() => {
    vi.mocked(placeGoldBubble).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('formatPlaceGoldBubbleError collapses TypeError to gameserver unreachable copy', () => {
    expect(formatPlaceGoldBubbleError(new TypeError('Failed to fetch'))).toMatch(
      /could not reach the gameserver/i,
    );
  });

  it('formatPlaceGoldBubbleError collapses axios Network Error to gameserver unreachable copy', () => {
    expect(formatPlaceGoldBubbleError(new Error('Network Error'))).toMatch(
      /could not reach the gameserver/i,
    );
  });

  it('formatPlaceGoldBubbleError surfaces 403 with GALAXY_MANAGE hint', () => {
    const err = { response: { status: 403, data: {} } };
    expect(formatPlaceGoldBubbleError(err)).toMatch(/admin\.galaxy\.manage/i);
  });

  it('formatPlaceGoldBubbleError surfaces 429 as admin rate-limit copy', () => {
    const collapsed = formatPlaceGoldBubbleError(axiosError(429));
    expect(collapsed).toMatch(/rate limit/i);
    expect(collapsed).not.toMatch(/\b429\b/);
    expect(collapsed).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(collapsed);
  });

  async function submitValidForm() {
    const user = userEvent.setup();
    const interiors = makeInteriorIds(GOLD_BUBBLE_INTERIOR_SIZE_MIN);
    render(<PlaceGoldBubblePanel />);
    fireEvent.change(screen.getByLabelText(/Region UUID/i), {
      target: { value: regionId },
    });
    fireEvent.change(screen.getByLabelText(/Gateway sector UUIDs/i), {
      target: { value: gateway },
    });
    fireEvent.change(screen.getByLabelText(/Interior sector UUIDs/i), {
      target: { value: interiors.join('\n') },
    });
    await user.click(screen.getByRole('button', { name: /Place Gold Bubble/i }));
  }

  it('collapses axios Network Error on submit to gameserver unreachable copy', async () => {
    vi.mocked(placeGoldBubble).mockRejectedValue(new Error('Network Error'));
    await submitValidForm();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/could not reach the gameserver/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses TypeError on submit to gameserver unreachable copy', async () => {
    vi.mocked(placeGoldBubble).mockRejectedValue(new TypeError('Failed to fetch'));
    await submitValidForm();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/could not reach the gameserver/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('surfaces 429 as admin rate-limit copy on placeGoldBubble submit', async () => {
    vi.mocked(placeGoldBubble).mockRejectedValue(axiosError(429));
    await submitValidForm();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });
});
