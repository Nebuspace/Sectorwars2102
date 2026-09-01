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

describe('PlaceGoldBubblePanel (LEG-184)', () => {
  const regionId = '11111111-1111-4111-8111-111111111111';
  const gateway = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

  beforeEach(() => {
    vi.mocked(placeGoldBubble).mockReset();
  });

  it('formatPlaceGoldBubbleError surfaces Network Error honestly', () => {
    expect(formatPlaceGoldBubbleError(new Error('Network Error'))).toMatch(
      /could not reach the gameserver/i,
    );
  });

  it('formatPlaceGoldBubbleError surfaces 403 with GALAXY_MANAGE hint', () => {
    const err = {
      response: { status: 403, data: {} },
    };
    expect(formatPlaceGoldBubbleError(err)).toMatch(/admin\.galaxy\.manage/i);
  });

  it('blocks submit when interior count is below canon minimum', async () => {
    const user = userEvent.setup();
    render(<PlaceGoldBubblePanel />);

    fireEvent.change(screen.getByLabelText(/Region UUID/i), {
      target: { value: regionId },
    });
    fireEvent.change(screen.getByLabelText(/Gateway sector UUIDs/i), {
      target: { value: gateway },
    });
    fireEvent.change(screen.getByLabelText(/Interior sector UUIDs/i), {
      target: { value: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' },
    });
    await user.click(screen.getByRole('button', { name: /Place Gold Bubble/i }));

    expect(placeGoldBubble).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toMatch(
      new RegExp(`≥${GOLD_BUBBLE_INTERIOR_SIZE_MIN}`),
    );
  });

  it('posts PlaceGoldBubbleRequest and shows success formation', async () => {
    const user = userEvent.setup();
    const interiors = makeInteriorIds(GOLD_BUBBLE_INTERIOR_SIZE_MIN);
    const formation = {
      id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      type: 'GOLD_BUBBLE',
      name: 'Ops Bubble',
      region_id: regionId,
      anchor_sector_id: gateway,
      interior_sector_ids: interiors,
      properties: {},
      discovery_requirement: null,
    };
    vi.mocked(placeGoldBubble).mockResolvedValue({
      success: true,
      formation,
    });

    render(
      <PlaceGoldBubblePanel
        regions={[{ id: regionId, display_name: 'Fringe Alpha' }]}
      />,
    );

    await user.selectOptions(
      screen.getByLabelText(/Select region for Gold Bubble/i),
      regionId,
    );
    fireEvent.change(screen.getByLabelText(/Gateway sector UUIDs/i), {
      target: { value: gateway },
    });
    fireEvent.change(screen.getByLabelText(/Interior sector UUIDs/i), {
      target: { value: interiors.join('\n') },
    });
    fireEvent.change(screen.getByLabelText(/^Name \(optional\)/i), {
      target: { value: 'Ops Bubble' },
    });
    await user.click(screen.getByRole('button', { name: /Place Gold Bubble/i }));

    await waitFor(() => {
      expect(placeGoldBubble).toHaveBeenCalledWith(regionId, {
        gateway_sector_ids: [gateway],
        interior_sector_ids: interiors,
        isolate_warps: true,
        name: 'Ops Bubble',
      });
    });
    expect(screen.getByRole('status').textContent).toMatch(/GOLD_BUBBLE/);
    expect(screen.getByRole('status').textContent).toMatch(formation.id);
  });

  it('surfaces 409 conflict detail from gameserver', async () => {
    const user = userEvent.setup();
    const interiors = makeInteriorIds(GOLD_BUBBLE_INTERIOR_SIZE_MIN);
    vi.mocked(placeGoldBubble).mockRejectedValue({
      response: {
        status: 409,
        data: { detail: 'Formation overlaps existing bubble family.' },
      },
    });

    render(<PlaceGoldBubblePanel />);
    fireEvent.change(screen.getByLabelText(/Region UUID/i), {
      target: { value: regionId },
    });
    fireEvent.change(screen.getByLabelText(/Gateway sector UUIDs/i), {
      target: { value: gateway },
    });
    fireEvent.change(screen.getByLabelText(/Interior sector UUIDs/i), {
      target: { value: interiors.join(',') },
    });
    await user.click(screen.getByRole('button', { name: /Place Gold Bubble/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/overlaps existing/i);
    });
  });
});
