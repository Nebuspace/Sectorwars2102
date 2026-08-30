// @vitest-environment jsdom
/**
 * useColonySpecialization — requirements gate + specialize API success/error.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const specializePlanet = vi.fn();
vi.mock('../../../services/api', () => ({
  gameAPI: {
    planetary: {
      specializePlanet: (...args: unknown[]) => specializePlanet(...args),
    },
  },
}));

import { SPECIALIZATIONS, formatColonySpecializeError, useColonySpecialization } from '../ColonySpecialization';
import type { Planet } from '../../../types/planetary';

describe('formatColonySpecializeError', () => {
  it('preserves 400 server detail from specialize PUT', () => {
    const err = new Error('Planet not found or not owned by player');
    (err as { status?: number }).status = 400;
    expect(formatColonySpecializeError(err)).toBe('Planet not found or not owned by player');
  });

  it('falls back when only bare API Error status is present', () => {
    expect(formatColonySpecializeError(new Error('API Error: 400'))).toBe(
      'Failed to specialize colony',
    );
  });

  it('falls back on TypeError network collapse (LEG-3044)', () => {
    const text = formatColonySpecializeError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to specialize colony/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

const agri = SPECIALIZATIONS.find((s) => s.type === 'agricultural')!;

const planet = {
  id: 'p1',
  name: 'Kepler',
  colonists: 5000,
  buildings: [{ type: 'farm', level: 1 }],
  specialization: null,
} as unknown as Planet;

type HookApi = ReturnType<typeof useColonySpecialization>;
let api: HookApi | null = null;

const Probe: React.FC<{ p?: Planet }> = ({ p = planet }) => {
  api = useColonySpecialization(p, vi.fn(), vi.fn());
  const req = api.meetsRequirements(agri);
  return (
    <div
      data-testid="probe"
      data-meets={String(req.meets)}
      data-missing={req.missing.join('|')}
      data-error={api.error ?? ''}
      data-success={api.successMessage ?? ''}
      data-changing={String(api.changing)}
    />
  );
};

describe('useColonySpecialization', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    api = null;
    specializePlanet.mockResolvedValue({ success: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it('lists five specializations with trade-off benefits', () => {
    expect(SPECIALIZATIONS).toHaveLength(5);
    expect(SPECIALIZATIONS.every((s) => s.benefits.length > 0)).toBe(true);
  });

  it('reports missing colonists and building levels', async () => {
    await act(async () => {
      root.render(<Probe />);
    });
    const probe = container.querySelector('[data-testid="probe"]') as HTMLElement;
    expect(probe.dataset.meets).toBe('false');
    expect(probe.dataset.missing).toContain('10,000 colonists');
    expect(probe.dataset.missing).toContain('farm level 2');
  });

  it('specializes when requirements are met', async () => {
    const rich = {
      ...planet,
      colonists: 20_000,
      buildings: [{ type: 'farm', level: 3 }],
    } as unknown as Planet;
    const onUpdate = vi.fn();
    const onClose = vi.fn();

    const RichProbe: React.FC = () => {
      api = useColonySpecialization(rich, onUpdate, onClose);
      return (
        <button
          type="button"
          data-testid="go"
          onClick={() => {
            void api?.setSelectedSpec('agricultural');
            void api?.handleSpecialize();
          }}
        >
          go
        </button>
      );
    };

    await act(async () => {
      root.render(<RichProbe />);
    });
    await act(async () => {
      api!.setSelectedSpec('agricultural');
    });
    await act(async () => {
      await api!.handleSpecialize();
    });

    expect(specializePlanet).toHaveBeenCalledWith('p1', 'agricultural');
    expect(onUpdate).toHaveBeenCalled();
    expect(api!.successMessage).toContain('Agricultural Colony');

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('errors when specializing to the current specialty', async () => {
    const already = {
      ...planet,
      colonists: 20_000,
      buildings: [{ type: 'farm', level: 3 }],
      specialization: 'agricultural',
    } as unknown as Planet;

    await act(async () => {
      root.render(<Probe p={already} />);
    });
    await act(async () => {
      api!.setSelectedSpec('agricultural');
      await api!.handleSpecialize();
    });
    expect(specializePlanet).not.toHaveBeenCalled();
    expect(api!.error).toContain('already specialized');
  });

  it('surfaces 400 server detail when specialize API refuses', async () => {
    const rich = {
      ...planet,
      colonists: 20_000,
      buildings: [{ type: 'farm', level: 3 }],
    } as unknown as Planet;
    specializePlanet.mockRejectedValueOnce(
      new Error('Planet not found or not owned by player'),
    );

    await act(async () => {
      root.render(<Probe p={rich} />);
    });
    await act(async () => {
      api!.setSelectedSpec('agricultural');
    });
    await act(async () => {
      await api!.handleSpecialize();
    });

    expect(specializePlanet).toHaveBeenCalledWith('p1', 'agricultural');
    expect(api!.error).toBe('Planet not found or not owned by player');
  });

  it('surfaces fallback when specialize API fails with bare API Error status', async () => {
    const rich = {
      ...planet,
      colonists: 20_000,
      buildings: [{ type: 'farm', level: 3 }],
    } as unknown as Planet;
    specializePlanet.mockRejectedValueOnce(new Error('API Error: 400'));

    await act(async () => {
      root.render(<Probe p={rich} />);
    });
    await act(async () => {
      api!.setSelectedSpec('agricultural');
    });
    await act(async () => {
      await api!.handleSpecialize();
    });

    expect(api!.error).toBe('Failed to specialize colony');
  });
});
