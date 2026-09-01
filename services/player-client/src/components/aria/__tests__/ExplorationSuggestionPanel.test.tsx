// @vitest-environment jsdom
/**
 * LEG-46 — ExplorationSuggestionPanel Vitest.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockGetSuggestions = vi.fn();

vi.mock('../../../services/api', () => ({
  ariaExplorationAPI: {
    getSuggestions: (...args: unknown[]) => mockGetSuggestions(...args),
  },
}));

import ExplorationSuggestionPanel, {
  formatExplorationSuggestionError,
} from '../ExplorationSuggestionPanel';

describe('formatExplorationSuggestionError (LEG-46)', () => {
  it('maps fetch TypeError to stable fallback', () => {
    expect(formatExplorationSuggestionError(new TypeError('Failed to fetch'))).toBe(
      'Failed to load exploration suggestions',
    );
  });
});

describe('ExplorationSuggestionPanel', () => {
  let container: HTMLElement;
  let root: Root;

  beforeEach(() => {
    mockGetSuggestions.mockReset();
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

  it('renders suggestions from the API', async () => {
    mockGetSuggestions.mockResolvedValue({
      suggestions: [
        {
          kind: 'repeat_visit',
          sector_id: 's1',
          summary: 'Sector 42 (Auriga) — visited 5 times with strong trade signals.',
        },
      ],
      empty_message: null,
    });

    await act(async () => {
      root.render(<ExplorationSuggestionPanel />);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockGetSuggestions).toHaveBeenCalled();
    expect(container.textContent).toContain('Sector 42');
    expect(container.textContent).toContain('repeat');
  });
});
