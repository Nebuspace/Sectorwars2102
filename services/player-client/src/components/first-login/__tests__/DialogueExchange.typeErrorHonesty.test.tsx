// @vitest-environment jsdom
/**
 * LEG-3204 Soft-ORDER — DialogueExchange submit-error TypeError honesty.
 * LEG-3404 Soft-ORDER — axios-shaped Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const submitResponse = vi.fn();

const firstLoginState = {
  currentPrompt: null,
  dialogueHistory: [
    {
      npc: 'State your business.',
      player: null,
      consistency: null,
      confidence: null,
      persuasiveness: null,
    },
  ],
  submitResponse,
  isLoading: false,
  dialogueOutcome: null,
  session: { id: 'sess-1' },
};

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => firstLoginState,
}));

import DialogueExchange, { formatDialogueSubmitError } from '../DialogueExchange';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('formatDialogueSubmitError TypeError honesty (LEG-3204)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatDialogueSubmitError(
      new TypeError('Failed to fetch'),
      'Failed to send your response. Please try again.',
    );
    expect(text).toBe('Failed to send your response. Please try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-TypeError Error message', () => {
    expect(
      formatDialogueSubmitError(new Error('ERR_DIALOGUE_CLOSED'), 'Failed to send your response. Please try again.'),
    ).toBe('ERR_DIALOGUE_CLOSED');
  });

  it('falls back on axios-shaped Network Error (LEG-3404)', () => {
    const fallback = 'Failed to send your response. Please try again.';
    const text = formatDialogueSubmitError(new Error('Network Error'), fallback);
    expect(text).toBe(fallback);
    expect(text).not.toMatch(/Network Error/i);
  });
});

describe('DialogueExchange submit TypeError honesty (LEG-3204)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    firstLoginState.isLoading = false;
    firstLoginState.dialogueOutcome = null;
    submitResponse.mockRejectedValue(new TypeError('Failed to fetch'));

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

  const mount = async () => {
    await act(async () => {
      root.render(<DialogueExchange />);
    });
    await flush();
  };

  it('submit TypeError surfaces honest fallback without Failed to fetch / TypeError', async () => {
    await mount();

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        'value',
      )?.set;
      setter?.call(textarea, 'I am a legitimate trader.');
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await act(async () => {
      (container.querySelector('button.submit-response') as HTMLButtonElement).click();
    });
    await flush();

    const alert = container.querySelector('.error-message[role="alert"]');
    expect(alert?.textContent).toContain('Failed to send your response. Please try again.');
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
