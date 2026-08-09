// @vitest-environment jsdom
/**
 * ContactActionMenu — portal menu: items, Escape close, item select.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ContactActionMenu from '../ContactActionMenu';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('ContactActionMenu', () => {
  let mountPoint: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let anchor: HTMLButtonElement;

  beforeEach(() => {
    mountPoint = document.createElement('div');
    document.body.appendChild(mountPoint);
    root = createRoot(mountPoint);
    anchor = document.createElement('button');
    anchor.textContent = 'Raven';
    document.body.appendChild(anchor);
    anchor.getBoundingClientRect = () =>
      ({
        left: 40,
        top: 80,
        right: 100,
        bottom: 100,
        width: 60,
        height: 20,
        x: 40,
        y: 80,
        toJSON: () => ({}),
      }) as DOMRect;
    vi.spyOn(performance, 'now').mockReturnValue(10_000);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    mountPoint.remove();
    anchor.remove();
    document.querySelectorAll('.contact-action-menu').forEach((el) => el.remove());
    vi.restoreAllMocks();
  });

  it('renders menu items and selects one', async () => {
    const onEngage = vi.fn();
    const onClose = vi.fn();
    await act(async () => {
      root.render(
        <ContactActionMenu
          anchorEl={anchor}
          label="Contact actions"
          onClose={onClose}
          items={[
            {
              key: 'engage',
              label: 'ENGAGE',
              variant: 'engage',
              title: 'Costs 1 turn',
              onSelect: onEngage,
            },
            { key: 'hail', label: 'HAIL', variant: 'hail', onSelect: vi.fn() },
          ]}
        />,
      );
    });

    const menu = document.querySelector('.contact-action-menu');
    expect(menu?.getAttribute('role')).toBe('menu');
    expect(menu?.getAttribute('aria-label')).toBe('Contact actions');

    const engage = Array.from(document.querySelectorAll('[role="menuitem"]')).find((el) =>
      el.textContent?.includes('ENGAGE'),
    ) as HTMLButtonElement;
    expect(engage.classList.contains('contact-action-menu-item-engage')).toBe(true);
    expect(engage.getAttribute('aria-label')).toBe('ENGAGE — Costs 1 turn');
    expect(engage.title).toBe('Costs 1 turn');

    await act(async () => {
      engage.click();
    });
    expect(onEngage).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape', async () => {
    const onClose = vi.fn();
    await act(async () => {
      root.render(
        <ContactActionMenu
          anchorEl={anchor}
          label="Contact actions"
          onClose={onClose}
          items={[{ key: 'hail', label: 'HAIL', onSelect: vi.fn() }]}
        />,
      );
    });

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on outside mousedown after the mount grace window', async () => {
    const onClose = vi.fn();
    let now = 10_000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);

    await act(async () => {
      root.render(
        <ContactActionMenu
          anchorEl={anchor}
          label="Contact actions"
          onClose={onClose}
          items={[{ key: 'hail', label: 'HAIL', onSelect: vi.fn() }]}
        />,
      );
    });

    now = 10_200; // past the 150ms mount grace
    await act(async () => {
      document.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
