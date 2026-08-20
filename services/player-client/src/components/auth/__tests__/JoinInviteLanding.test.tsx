// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import JoinInviteLanding from '../JoinInviteLanding';
import { REGION_INVITE_STORAGE_KEY } from '../regionInvite';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('JoinInviteLanding', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    sessionStorage.clear();
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

  it('persists ?invite= and navigates home', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/join?invite=OwnerCode_9']}>
          <Routes>
            <Route path="/join" element={<JoinInviteLanding />} />
            <Route path="/" element={<div data-testid="home">home</div>} />
          </Routes>
        </MemoryRouter>,
      );
    });
    expect(sessionStorage.getItem(REGION_INVITE_STORAGE_KEY)).toBe('OwnerCode_9');
    expect(container.querySelector('[data-testid="home"]')?.textContent).toBe('home');
  });

  it('does not persist an unsanitary invite', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/join?invite=<img>']}>
          <Routes>
            <Route path="/join" element={<JoinInviteLanding />} />
            <Route path="/" element={<div data-testid="home">home</div>} />
          </Routes>
        </MemoryRouter>,
      );
    });
    expect(sessionStorage.getItem(REGION_INVITE_STORAGE_KEY)).toBeNull();
  });
});
