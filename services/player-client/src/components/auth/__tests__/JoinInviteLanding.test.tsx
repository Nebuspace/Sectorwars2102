// @vitest-environment jsdom
/**
 * LEG-3151 — JoinInviteLanding /join route on-ramp.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import JoinInviteLanding from '../JoinInviteLanding';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}{location.search}</div>;
}

function RegisterStub() {
  const location = useLocation();
  return (
    <div data-testid="register-stub">
      {location.pathname}
      {location.search}
    </div>
  );
}

const renderAt = async (initial: string) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/join" element={<JoinInviteLanding />} />
          <Route path="/register" element={<RegisterStub />} />
          <Route path="/" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return { container, root };
};

describe('JoinInviteLanding (LEG-3151)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  afterEach(async () => {
    if (root) {
      await act(async () => {
        root.unmount();
      });
    }
    if (container) {
      container.remove();
    }
  });

  it('renders invite code from query on /join?invite=CODE', async () => {
    ({ container, root } = await renderAt('/join?invite=REGION-ABC'));
    const codeBlock = container.querySelector('[data-testid="join-invite-code"]');
    expect(codeBlock?.textContent).toContain('REGION-ABC');
    expect(container.querySelector('[data-testid="join-invite-missing"]')).toBeNull();
  });

  it('shows honest copy when invite param is missing', async () => {
    ({ container, root } = await renderAt('/join'));
    expect(container.querySelector('[data-testid="join-invite-missing"]')).not.toBeNull();
    expect(container.textContent).toMatch(/missing an invite code/i);
    expect(container.querySelector('[data-testid="join-invite-code"]')).toBeNull();
  });

  it('primary CTA navigates to register with invite prefilled', async () => {
    ({ container, root } = await renderAt('/join?invite=REGION-ABC'));
    const cta = container.querySelector('[data-testid="join-invite-register-cta"]') as HTMLButtonElement;
    await act(async () => {
      cta.click();
    });
    expect(container.querySelector('[data-testid="register-stub"]')).not.toBeNull();
    const stub = container.querySelector('[data-testid="register-stub"]');
    expect(stub?.textContent).toContain('/register?invite=REGION-ABC');
  });
});
