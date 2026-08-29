// @vitest-environment jsdom
/**
 * ProposePolicyForm — regional-governance policy proposal form. jsdom +
 * react-dom/client createRoot + act(), no RTL, no new deps — matches the
 * LoginForm/DefenseConfiguration form-interaction seam: native value setter
 * + dispatchEvent('input'/'change') to drive controlled inputs.
 *
 * Pins: buildProposedChanges' float/int coercion + blank-field omission,
 * the "other" custom-type reveal, both client-side required-field guards,
 * the trade-bonus row add/remove/patch + incomplete-row omission, the three
 * distinct error-handling branches (fieldErrors array vs Error vs unknown),
 * the submitting-state disables-everything gate, and the odd-but-real
 * createdPolicyId-stays-null-on-a-policy_id-less-response edge case (the
 * success view never appears even though onCreated() still fires).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockProposePolicy } = vi.hoisted(() => ({
  mockProposePolicy: vi.fn<
    (regionId: string, data: Record<string, unknown>) => Promise<{ policy_id?: string }>
  >(async () => ({ policy_id: 'policy-1' })),
}));

vi.mock('../../../services/api', () => ({
  governanceAPI: { proposePolicy: mockProposePolicy },
}));

import ProposePolicyForm, { formatProposePolicyError } from '../ProposePolicyForm';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

function setValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

function setSelectValue(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

describe('ProposePolicyForm', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onCreated: () => void;
  let onCancel: () => void;

  beforeEach(() => {
    mockProposePolicy.mockReset();
    mockProposePolicy.mockResolvedValue({ policy_id: 'policy-1' });
    onCreated = vi.fn<() => void>();
    onCancel = vi.fn<() => void>();
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
      root.render(<ProposePolicyForm regionId="region-1" onCreated={onCreated} onCancel={onCancel} />);
    });
  };

  const fieldRow = (labelText: string): HTMLElement => {
    const rows = Array.from(container.querySelectorAll('.gov-form-row'));
    const row = rows.find((r) => r.querySelector('label')?.textContent?.startsWith(labelText));
    if (!row) throw new Error(`no field row for label "${labelText}"`);
    return row as HTMLElement;
  };
  const titleInput = () => fieldRow('Title').querySelector('input') as HTMLInputElement;
  const submitBtn = () => container.querySelector('.gov-btn.primary') as HTMLButtonElement;
  const cancelBtn = () => container.querySelector('.gov-confirm-row .gov-btn.ghost') as HTMLButtonElement;
  const policyTypeSelect = () => fieldRow('Policy type').querySelector('select') as HTMLSelectElement;

  const fillTitle = async (value: string) => {
    await act(async () => setValue(titleInput(), value));
  };

  const submit = async () => {
    await act(async () => {
      submitBtn().click();
    });
    await flush();
  };

  it('renders the form with the first policy-type suggestion selected and a 7-day default voting duration', async () => {
    await mount();
    expect(container.querySelector('h3')?.textContent).toBe('PROPOSE POLICY');
    expect(policyTypeSelect().value).toBe('tax_rate');
    expect((container.querySelector('input[type="number"][min="1"]') as HTMLInputElement).value).toBe('7');
  });

  it('reveals a custom policy-type input only when "other" is selected', async () => {
    await mount();
    expect(container.querySelector('input[placeholder="Custom policy type"]')).toBeNull();

    await act(async () => setSelectValue(policyTypeSelect(), 'other'));
    expect(container.querySelector('input[placeholder="Custom policy type"]')).not.toBeNull();
  });

  it('blocks submit with "Policy type is required." when "other" is selected but the custom field is blank', async () => {
    await mount();
    await act(async () => setSelectValue(policyTypeSelect(), 'other'));
    await fillTitle('My Policy');
    await submit();

    expect(container.querySelector('.gov-validation-strip')?.textContent).toBe('Policy type is required.');
    expect(mockProposePolicy).not.toHaveBeenCalled();
  });

  it('blocks submit with "Title is required." when title is blank', async () => {
    await mount();
    await submit();

    expect(container.querySelector('.gov-validation-strip')?.textContent).toBe('Title is required.');
    expect(mockProposePolicy).not.toHaveBeenCalled();
  });

  it('submits with the default policy type, trimmed title, empty changes, and parsed voting duration', async () => {
    await mount();
    await fillTitle('  New Tax Policy  ');
    await submit();

    expect(mockProposePolicy).toHaveBeenCalledWith('region-1', {
      policy_type: 'tax_rate',
      title: 'New Tax Policy',
      description: undefined,
      proposed_changes: {},
      voting_duration_days: 7,
    });
  });

  it('submits the custom policy type when "other" is selected and filled', async () => {
    await mount();
    await act(async () => setSelectValue(policyTypeSelect(), 'other'));
    await act(async () =>
      setValue(container.querySelector('input[placeholder="Custom policy type"]') as HTMLInputElement, 'my_custom_type')
    );
    await fillTitle('Custom');
    await submit();

    expect(mockProposePolicy.mock.calls[0][1]).toMatchObject({ policy_type: 'my_custom_type' });
  });

  it('omits description when blank and includes it trimmed when present', async () => {
    await mount();
    await fillTitle('T');
    await act(async () =>
      setValue(container.querySelector('textarea') as HTMLTextAreaElement, '  some detail  ')
    );
    await submit();

    expect(mockProposePolicy.mock.calls[0][1]).toMatchObject({ description: 'some detail' });
  });

  it('omits voting_duration_days when the field is cleared', async () => {
    await mount();
    await fillTitle('T');
    await act(async () =>
      setValue(container.querySelector('input[type="number"][min="1"]') as HTMLInputElement, '')
    );
    await submit();

    expect(mockProposePolicy.mock.calls[0][1]).toMatchObject({ voting_duration_days: undefined });
  });

  it('builds proposed_changes with float/int-coerced guided fields, omitting blank ones', async () => {
    await mount();
    await fillTitle('T');
    const numberInputs = Array.from(container.querySelectorAll('.gov-changes-editor input[type="number"]')) as HTMLInputElement[];
    // Order: tax rate, voting threshold, election frequency, quorum.
    await act(async () => setValue(numberInputs[0], '0.15'));
    await act(async () => setValue(numberInputs[2], '90'));
    await submit();

    expect(mockProposePolicy.mock.calls[0][1].proposed_changes).toEqual({
      tax_rate: 0.15,
      election_frequency_days: 90,
    });
  });

  it('includes governance_type as a raw string when selected', async () => {
    await mount();
    await fillTitle('T');
    const govSelect = Array.from(container.querySelectorAll('select')).find((s) => s !== policyTypeSelect()) as HTMLSelectElement;
    await act(async () => setSelectValue(govSelect, 'democracy'));
    await submit();

    expect(mockProposePolicy.mock.calls[0][1].proposed_changes).toEqual({ governance_type: 'democracy' });
  });

  it('adds a trade-bonus row and includes it in proposed_changes once both fields are filled', async () => {
    await mount();
    await fillTitle('T');
    await act(async () => {
      (container.querySelector('.gov-trade-bonus-editor .gov-btn.ghost.small') as HTMLButtonElement).click();
    });
    const rowInputs = container.querySelectorAll('.gov-trade-bonus-row input');
    expect(rowInputs.length).toBe(2);

    await act(async () => setValue(rowInputs[0] as HTMLInputElement, 'ORE'));
    await act(async () => setValue(rowInputs[1] as HTMLInputElement, '1.5'));
    await submit();

    expect(mockProposePolicy.mock.calls[0][1].proposed_changes).toEqual({ trade_bonuses: { ORE: 1.5 } });
  });

  it('removes a trade-bonus row on REMOVE, dropping it back out of the form', async () => {
    await mount();
    await act(async () => {
      (container.querySelector('.gov-trade-bonus-editor .gov-btn.ghost.small') as HTMLButtonElement).click();
    });
    expect(container.querySelectorAll('.gov-trade-bonus-row').length).toBe(1);

    await act(async () => {
      (container.querySelector('.gov-trade-bonus-row .gov-btn.ghost.small') as HTMLButtonElement).click();
    });
    expect(container.querySelectorAll('.gov-trade-bonus-row').length).toBe(0);
  });

  it('omits an incomplete trade-bonus row (only resource or only bonus filled)', async () => {
    await mount();
    await fillTitle('T');
    await act(async () => {
      (container.querySelector('.gov-trade-bonus-editor .gov-btn.ghost.small') as HTMLButtonElement).click();
    });
    const rowInputs = container.querySelectorAll('.gov-trade-bonus-row input');
    await act(async () => setValue(rowInputs[0] as HTMLInputElement, 'ORE'));
    // bonus left blank
    await submit();

    expect(mockProposePolicy.mock.calls[0][1].proposed_changes).toEqual({});
  });

  it('shows the success view and calls onCreated when the response carries a policy_id', async () => {
    await mount();
    await fillTitle('T');
    await submit();

    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(container.querySelector('.gov-propose-success .gov-success-note')?.textContent).toBe(
      'Policy proposal created — voting is now open.'
    );
    expect(container.querySelector('h3')).toBeNull();

    await act(async () => {
      (container.querySelector('.gov-propose-success button') as HTMLButtonElement).click();
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('stays on the form (no success view) when the response has no policy_id, though onCreated still fires', async () => {
    mockProposePolicy.mockResolvedValueOnce({});
    await mount();
    await fillTitle('T');
    await submit();

    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(container.querySelector('.gov-propose-success')).toBeNull();
    expect(container.querySelector('h3')?.textContent).toBe('PROPOSE POLICY');
  });

  it('renders a fieldErrors list (not a single submitError) when the rejection carries a string-array errors field', async () => {
    mockProposePolicy.mockRejectedValueOnce({ errors: ['Title too long', 'Invalid policy type'] });
    await mount();
    await fillTitle('T');
    await submit();

    const items = Array.from(container.querySelectorAll('.gov-validation-list li')).map((li) => li.textContent);
    expect(items).toEqual(['Title too long', 'Invalid policy type']);
    expect(container.querySelector('.gov-validation-strip:not(.gov-validation-list)')).toBeNull();
  });

  it('renders err.message as submitError for a plain Error rejection', async () => {
    mockProposePolicy.mockRejectedValueOnce(new Error('region is not accepting proposals'));
    await mount();
    await fillTitle('T');
    await submit();

    expect(container.querySelector('.gov-validation-strip')?.textContent).toBe('region is not accepting proposals');
  });

  it('falls back to a generic message for a rejection that is neither an Error nor a string-array errors field', async () => {
    mockProposePolicy.mockRejectedValueOnce('network exploded');
    await mount();
    await fillTitle('T');
    await submit();

    expect(container.querySelector('.gov-validation-strip')?.textContent).toBe('Failed to propose policy.');
  });

  it('formatProposePolicyError preserves gameserver detail on reject (LEG-2945)', () => {
    const err = Object.assign(new Error('region is not accepting proposals'), { status: 400 });
    expect(formatProposePolicyError(err)).toBe('region is not accepting proposals');
  });

  it('formatProposePolicyError uses 429 rate-limit copy when detail absent (LEG-2945)', () => {
    const err = Object.assign(new Error('API Error: 429'), { status: 429 });
    expect(formatProposePolicyError(err)).toBe(
      'Policy proposal rate limit exceeded — wait a moment and try again.',
    );
  });

  it('formatProposePolicyError uses 403 fallback when detail absent (LEG-2945)', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatProposePolicyError(err)).toBe(
      'You do not have permission to propose a policy in this region.',
    );
  });

  it('surfaces 429 rate-limit honest copy on submit reject (LEG-2945)', async () => {
    mockProposePolicy.mockRejectedValueOnce(
      Object.assign(new Error('API Error: 429'), { status: 429 }),
    );
    await mount();
    await fillTitle('T');
    await submit();

    expect(container.querySelector('.gov-validation-strip')?.textContent).toBe(
      'Policy proposal rate limit exceeded — wait a moment and try again.',
    );
  });

  it('disables the submit button and shows SUBMITTING… while the request is in flight', async () => {
    let resolveFn: (v: unknown) => void = () => {};
    mockProposePolicy.mockImplementationOnce(() => new Promise((resolve) => { resolveFn = resolve; }));
    await mount();
    await fillTitle('T');

    await act(async () => {
      submitBtn().click();
    });
    expect(submitBtn().disabled).toBe(true);
    expect(submitBtn().textContent).toBe('SUBMITTING…');
    expect(titleInput().disabled).toBe(true);

    await act(async () => {
      resolveFn({ policy_id: 'policy-2' });
    });
    await flush();
  });

  it('calls onCancel when CANCEL is clicked', async () => {
    await mount();
    await act(async () => {
      cancelBtn().click();
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
