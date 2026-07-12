// @vitest-environment jsdom
/**
 * DeckPageTabs — live-mount interaction proof (WO-UI2-DECK-MONITORS).
 *
 * Mirrors StatusBar.smoke.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL — this repo has no @testing-library/react
 * dependency and no test file uses it; this is the established pattern
 * this codebase actually proves components with).
 *
 * A thin controlled wrapper drives activeId from onSelect, exactly the way
 * GameDashboard.tsx (NAV/SOLAR SYSTEM) and CommsMailbox.tsx (COMMS) use
 * the real component — so aria-selected/roving-tabindex assertions reflect
 * an actual controlled round-trip, not a call-was-made spy alone.
 */
import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import DeckPageTabs, { type DeckPage } from '../DeckPageTabs';

// Same jsdom+createRoot+act harness quirk noted in StatusBar.smoke.test.tsx
// ("current testing environment is not configured to support act(...)") —
// baseline-wide in this repo, unrelated to DeckPageTabs itself.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface HarnessProps {
  pages: DeckPage[];
  initialId: string;
  onSelectSpy?: (id: string) => void;
}

// Mirrors the real parent contract (GameDashboard NAV/SOLAR SYSTEM,
// CommsMailbox): a single swapped tabpanel, id/aria-labelledby derived
// from the same idBase + activeId formula DeckPageTabs uses for its own
// id/aria-controls — so the full tablist→tabpanel loop (Pixel
// INACCESSIBLE) is provable end-to-end, not just DeckPageTabs in
// isolation.
const HARNESS_ID_BASE = 'test';

const Harness: React.FC<HarnessProps> = ({ pages, initialId, onSelectSpy }) => {
  const [activeId, setActiveId] = useState(initialId);
  return (
    <>
      <DeckPageTabs
        pages={pages}
        activeId={activeId}
        onSelect={(id) => {
          setActiveId(id);
          onSelectSpy?.(id);
        }}
        ariaLabel="TEST display mode"
        accent="#00d9ff"
        idBase={HARNESS_ID_BASE}
      />
      <div
        role="tabpanel"
        id={`${HARNESS_ID_BASE}-panel-${activeId}`}
        aria-labelledby={`${HARNESS_ID_BASE}-tab-${activeId}`}
      >
        {activeId}
      </div>
    </>
  );
};

describe('DeckPageTabs', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const flush = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  const mount = async (props: HarnessProps) => {
    await act(async () => {
      root.render(<Harness {...props} />);
    });
    await flush();
  };

  beforeEach(() => {
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

  it('renders NO rail when fewer than 2 pages are available (not a single dead tab)', async () => {
    await mount({
      pages: [
        { id: 'graph', label: 'WARP GRAPH' },
        { id: 'quantum', label: 'QUANTUM DRIVE', available: false },
      ],
      initialId: 'graph',
    });

    expect(container.querySelector('[role="tablist"]')).toBeNull();
    expect(container.querySelectorAll('[role="tab"]').length).toBe(0);
  });

  it('renders a rail with aria-selected reflecting activeId and roving tabindex on the active tab only', async () => {
    await mount({
      pages: [
        { id: 'bodies', label: 'BODIES' },
        { id: 'hazards', label: 'HAZARDS' },
      ],
      initialId: 'bodies',
    });

    const tablist = container.querySelector('[role="tablist"]');
    expect(tablist).not.toBeNull();
    expect(tablist?.getAttribute('aria-label')).toBe('TEST display mode');

    const tabs = Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    expect(tabs.length).toBe(2);
    expect(tabs[0].getAttribute('aria-selected')).toBe('true');
    expect(tabs[1].getAttribute('aria-selected')).toBe('false');
    expect(tabs[0].tabIndex).toBe(0);
    expect(tabs[1].tabIndex).toBe(-1);

    // Pixel INACCESSIBLE: every tab must carry a non-empty aria-controls
    // (and a matching id) so a screen reader can associate it with its panel.
    tabs.forEach((tab) => {
      expect(tab.id).toBeTruthy();
      expect(tab.getAttribute('aria-controls')).toBeTruthy();
    });
    expect(tabs[0].id).toBe('test-tab-bodies');
    expect(tabs[0].getAttribute('aria-controls')).toBe('test-panel-bodies');
    expect(tabs[1].id).toBe('test-tab-hazards');
    expect(tabs[1].getAttribute('aria-controls')).toBe('test-panel-hazards');
  });

  it('the tablist→tabpanel loop closes: the active tab\'s aria-controls matches the rendered tabpanel\'s id, and the tabpanel\'s aria-labelledby matches the active tab\'s id — round-trips on selection (Pixel INACCESSIBLE)', async () => {
    await mount({
      pages: [
        { id: 'contacts', label: 'CONTACTS' },
        { id: 'hails', label: 'HAILS' },
      ],
      initialId: 'contacts',
    });

    const getActiveTab = () =>
      Array.from(container.querySelectorAll('[role="tab"]')).find(
        (t) => t.getAttribute('aria-selected') === 'true'
      ) as HTMLButtonElement;
    const getTabpanel = () => container.querySelector('[role="tabpanel"]') as HTMLElement;

    let activeTab = getActiveTab();
    let panel = getTabpanel();
    expect(panel).not.toBeNull();
    expect(activeTab.getAttribute('aria-controls')).toBe(panel.id);
    expect(panel.getAttribute('aria-labelledby')).toBe(activeTab.id);

    const inactiveTab = Array.from(container.querySelectorAll('[role="tab"]')).find(
      (t) => t !== activeTab
    ) as HTMLButtonElement;
    await act(async () => {
      inactiveTab.click();
    });
    await flush();

    activeTab = getActiveTab();
    panel = getTabpanel();
    expect(activeTab.id).toBe(inactiveTab.id);
    expect(activeTab.getAttribute('aria-controls')).toBe(panel.id);
    expect(panel.getAttribute('aria-labelledby')).toBe(activeTab.id);
  });

  it('click selects a page: onSelect fires and aria-selected/tabindex flip (controlled round-trip)', async () => {
    const onSelectSpy = vi.fn();
    await mount({
      pages: [
        { id: 'contacts', label: 'CONTACTS' },
        { id: 'hails', label: 'HAILS' },
      ],
      initialId: 'contacts',
      onSelectSpy,
    });

    const tabs = Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    await act(async () => {
      tabs[1].click();
    });
    await flush();

    expect(onSelectSpy).toHaveBeenCalledWith('hails');
    const tabsAfter = Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    expect(tabsAfter[1].getAttribute('aria-selected')).toBe('true');
    expect(tabsAfter[0].getAttribute('aria-selected')).toBe('false');
    expect(tabsAfter[1].tabIndex).toBe(0);
    expect(tabsAfter[0].tabIndex).toBe(-1);
  });

  it('ArrowRight/ArrowLeft wrap across the rendered tabs, and focus follows the newly active tab', async () => {
    await mount({
      pages: [
        { id: 'graph', label: 'WARP GRAPH' },
        { id: 'quantum', label: 'QUANTUM DRIVE' },
      ],
      initialId: 'graph',
    });

    const tabs = () => Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];

    const press = async (key: string) => {
      await act(async () => {
        tabs()[0].dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
      });
      await flush();
    };
    // Redirect subsequent keydowns to whichever tab currently has focus,
    // matching real usage (the handler lives on each button).
    const pressOnFocused = async (key: string) => {
      const target = (document.activeElement as HTMLButtonElement) ?? tabs()[0];
      await act(async () => {
        target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
      });
      await flush();
    };

    await press('ArrowRight');
    expect(tabs()[1].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs()[1]);

    // Wraps from the last tab back to the first.
    await pressOnFocused('ArrowRight');
    expect(tabs()[0].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs()[0]);

    // Wraps from the first tab back to the last.
    await pressOnFocused('ArrowLeft');
    expect(tabs()[1].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs()[1]);
  });

  it('ArrowRight skips an unavailable page entirely (it is never rendered as a dead tab in between)', async () => {
    await mount({
      pages: [
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B', available: false },
        { id: 'c', label: 'C' },
      ],
      initialId: 'a',
    });

    const tabs = () => Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
    // Only 2 tabs ever render — 'b' is excluded outright, not disabled-in-place.
    expect(tabs().length).toBe(2);
    expect(tabs().map((t) => t.textContent)).toEqual(['A', 'C']);

    await act(async () => {
      tabs()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    });
    await flush();

    // Landed directly on 'c' — the only other rendered tab.
    expect(tabs()[1].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs()[1]);
  });

  it('Home/End jump to the first/last tab and move focus', async () => {
    await mount({
      pages: [
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B' },
        { id: 'c', label: 'C' },
      ],
      initialId: 'b',
    });

    const tabs = () => Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];

    await act(async () => {
      tabs()[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }));
    });
    await flush();
    expect(tabs()[2].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs()[2]);

    await act(async () => {
      tabs()[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true }));
    });
    await flush();
    expect(tabs()[0].getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(tabs()[0]);
  });

  it('supports a ReactNode label (e.g. an unread-dot badge alongside text)', async () => {
    await mount({
      pages: [
        { id: 'contacts', label: 'CONTACTS' },
        {
          id: 'hails',
          label: (
            <>
              HAILS
              <span className="test-dot" aria-hidden="true" />
            </>
          ),
        },
      ],
      initialId: 'contacts',
    });

    const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
    expect(tabs[1].querySelector('.test-dot')).not.toBeNull();
    expect(tabs[1].textContent).toBe('HAILS');
  });
});
