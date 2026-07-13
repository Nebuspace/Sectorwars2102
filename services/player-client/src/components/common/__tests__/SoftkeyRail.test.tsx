// @vitest-environment jsdom
/**
 * SoftkeyRail — live-mount proof that the ONE shared rail (WO-UI0-SHELL-
 * TRANSPLANT, register D7) correctly reproduces BOTH pre-existing
 * interaction models it now backs:
 *   - Deck (DeckPageTabs' config: activateOnArrow + homeEnd) — automatic
 *     activation, arrow/Home/End both move focus AND select.
 *   - MFD (mfd/MFDScreen's config: neither) — manual activation, arrows
 *     only move focus among non-disabled keys; a disabled key renders
 *     in-place (never filtered) and is skipped by arrow nav; selection
 *     needs native Enter/Space activation of the focused button.
 *
 * Mirrors DeckPageTabs.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL — this repo's established convention, see
 * that file's own doc-comment).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import SoftkeyRail, { type SoftkeyRailItem } from '../SoftkeyRail';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('SoftkeyRail', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const flush = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  const mount = async (el: React.ReactElement) => {
    await act(async () => {
      root.render(el);
    });
    await flush();
  };

  const tabs = () => Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];

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

  describe('Deck variant (activateOnArrow + homeEnd — DeckPageTabs config)', () => {
    const deckItems = (onSelect: (key: string) => void, activeKey: string): SoftkeyRailItem[] =>
      ['a', 'b', 'c'].map((key) => ({
        key,
        label: key.toUpperCase(),
        selected: key === activeKey,
        onSelect: () => onSelect(key),
        id: `t-tab-${key}`,
        ariaControls: `t-panel-${key}`,
      }));

    it('renders role=tablist/tab with aria-selected + roving tabindex on the active tab only', async () => {
      const onSelect = vi.fn();
      await mount(
        <SoftkeyRail
          items={deckItems(onSelect, 'b')}
          ariaLabel="TEST"
          railClassName="deck-tab-rail"
          itemClassName={(item) => `deck-tab-btn${item.selected ? ' active' : ''}`}
          accentVar="--tab-accent"
          railAccent="#00d9ff"
          activateOnArrow
          homeEnd
        />,
      );

      expect(container.querySelector('[role="tablist"]')?.getAttribute('aria-label')).toBe('TEST');
      const btns = tabs();
      expect(btns.length).toBe(3);
      expect(btns[1].getAttribute('aria-selected')).toBe('true');
      expect(btns[1].tabIndex).toBe(0);
      expect(btns[0].tabIndex).toBe(-1);
      expect(btns[2].tabIndex).toBe(-1);
      expect(btns[1].className).toBe('deck-tab-btn active');
      expect(btns[1].id).toBe('t-tab-b');
      expect(btns[1].getAttribute('aria-controls')).toBe('t-panel-b');
    });

    it('ArrowRight both moves focus AND fires onSelect immediately (automatic activation), wrapping past the end', async () => {
      const onSelect = vi.fn();
      const Harness: React.FC = () => {
        const [active, setActive] = React.useState('a');
        return (
          <SoftkeyRail
            items={deckItems((k) => {
              onSelect(k);
              setActive(k);
            }, active)}
            ariaLabel="TEST"
            railClassName="deck-tab-rail"
            itemClassName={(item) => `deck-tab-btn${item.selected ? ' active' : ''}`}
            accentVar="--tab-accent"
            railAccent="#00d9ff"
            activateOnArrow
            homeEnd
          />
        );
      };
      await mount(<Harness />);

      await act(async () => {
        tabs()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
      });
      await flush();
      expect(onSelect).toHaveBeenCalledWith('b');
      expect(tabs()[1].getAttribute('aria-selected')).toBe('true');
      expect(document.activeElement).toBe(tabs()[1]);

      await act(async () => {
        tabs()[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
      });
      await flush();
      expect(onSelect).toHaveBeenCalledWith('a');
      expect(tabs()[0].getAttribute('aria-selected')).toBe('true');
    });

    it('Home/End jump to the first/last item and select it', async () => {
      const onSelect = vi.fn();
      const Harness: React.FC = () => {
        const [active, setActive] = React.useState('b');
        return (
          <SoftkeyRail
            items={deckItems((k) => {
              onSelect(k);
              setActive(k);
            }, active)}
            ariaLabel="TEST"
            railClassName="deck-tab-rail"
            itemClassName={(item) => `deck-tab-btn${item.selected ? ' active' : ''}`}
            accentVar="--tab-accent"
            railAccent="#00d9ff"
            activateOnArrow
            homeEnd
          />
        );
      };
      await mount(<Harness />);

      await act(async () => {
        tabs()[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }));
      });
      await flush();
      expect(onSelect).toHaveBeenCalledWith('c');

      await act(async () => {
        tabs()[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true }));
      });
      await flush();
      expect(onSelect).toHaveBeenCalledWith('a');
    });

    it('a per-item accent overrides railAccent; an item without one falls back to railAccent (both at the button)', async () => {
      const onSelect = vi.fn();
      const items: SoftkeyRailItem[] = [
        { key: 'a', label: 'A', selected: true, onSelect: () => onSelect('a'), accent: '#fbbf24' },
        { key: 'b', label: 'B', selected: false, onSelect: () => onSelect('b') },
      ];
      await mount(
        <SoftkeyRail
          items={items}
          ariaLabel="TEST"
          railClassName="deck-tab-rail"
          itemClassName={() => 'deck-tab-btn'}
          accentVar="--tab-accent"
          railAccent="#00d9ff"
          activateOnArrow
          homeEnd
        />,
      );
      const btns = tabs();
      expect(btns[0].style.getPropertyValue('--tab-accent')).toBe('#fbbf24');
      expect(btns[1].style.getPropertyValue('--tab-accent')).toBe('#00d9ff');
      expect(
        (container.querySelector('[role="tablist"]') as HTMLElement).style.getPropertyValue('--tab-accent'),
      ).toBe('#00d9ff');
    });
  });

  describe('MFD variant (manual activation, disabled-in-place — MFDScreen config)', () => {
    const mfdItems = (onSelect: (key: string) => void, activeKey: string): SoftkeyRailItem[] => [
      { key: 'stat', label: 'STAT', selected: activeKey === 'stat', disabled: false, onSelect: () => onSelect('stat'), accent: '#00d9ff' },
      { key: 'cargo', label: 'CRGO', selected: activeKey === 'cargo', disabled: true, onSelect: () => onSelect('cargo'), accent: '#00d9ff' },
      { key: 'qtm', label: 'QTM', selected: activeKey === 'qtm', disabled: false, onSelect: () => onSelect('qtm'), accent: '#00d9ff' },
    ];

    it('renders a disabled item IN PLACE (never filtered) with disabled + aria-disabled', async () => {
      const onSelect = vi.fn();
      await mount(
        <SoftkeyRail
          items={mfdItems(onSelect, 'stat')}
          ariaLabel="MFD-A pages"
          railClassName="mfd-softkey-rail"
          itemClassName={() => 'mfd-key'}
          accentVar="--mfd-key-accent"
          activateOnArrow={false}
          homeEnd={false}
        />,
      );
      const btns = tabs();
      expect(btns.length).toBe(3);
      expect(btns[1].disabled).toBe(true);
      expect(btns[1].getAttribute('aria-disabled')).toBe('true');
      expect(btns[0].getAttribute('aria-disabled')).toBe('false');
      // No rail-level style attribute at all (MFD sets no rail-level accent).
      expect((container.querySelector('[role="tablist"]') as HTMLElement).getAttribute('style')).toBeNull();
    });

    it('ArrowRight moves focus WITHOUT selecting (manual activation) and SKIPS the disabled key', async () => {
      const onSelect = vi.fn();
      await mount(
        <SoftkeyRail
          items={mfdItems(onSelect, 'stat')}
          ariaLabel="MFD-A pages"
          railClassName="mfd-softkey-rail"
          itemClassName={() => 'mfd-key'}
          accentVar="--mfd-key-accent"
          activateOnArrow={false}
          homeEnd={false}
        />,
      );
      await act(async () => {
        tabs()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
      });
      await flush();

      // Focus skipped straight over the disabled 'cargo' key to 'qtm'.
      expect(document.activeElement).toBe(tabs()[2]);
      // Manual activation: no selection fired just from moving focus.
      expect(onSelect).not.toHaveBeenCalled();
      // aria-selected is still whatever the caller passed (unchanged --
      // this component never self-selects on arrow move).
      expect(tabs()[0].getAttribute('aria-selected')).toBe('true');
    });

    it('Home/End are inert when homeEnd=false (MFD never implemented them)', async () => {
      const onSelect = vi.fn();
      await mount(
        <SoftkeyRail
          items={mfdItems(onSelect, 'stat')}
          ariaLabel="MFD-A pages"
          railClassName="mfd-softkey-rail"
          itemClassName={() => 'mfd-key'}
          accentVar="--mfd-key-accent"
          activateOnArrow={false}
          homeEnd={false}
        />,
      );
      await act(async () => {
        tabs()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }));
      });
      await flush();
      expect(onSelect).not.toHaveBeenCalled();
      expect(document.activeElement).not.toBe(tabs()[2]);
    });

    it('native click still selects a disabled-adjacent enabled key (selection itself is untouched by the manual-activation model)', async () => {
      const onSelect = vi.fn();
      await mount(
        <SoftkeyRail
          items={mfdItems(onSelect, 'stat')}
          ariaLabel="MFD-A pages"
          railClassName="mfd-softkey-rail"
          itemClassName={() => 'mfd-key'}
          accentVar="--mfd-key-accent"
          activateOnArrow={false}
          homeEnd={false}
        />,
      );
      await act(async () => {
        tabs()[2].click();
      });
      await flush();
      expect(onSelect).toHaveBeenCalledWith('qtm');
    });

    it('tabStopIndex falls back to the first non-disabled item when no item is selected', async () => {
      const items: SoftkeyRailItem[] = [
        { key: 'cargo', label: 'CRGO', selected: false, disabled: true, onSelect: vi.fn() },
        { key: 'qtm', label: 'QTM', selected: false, disabled: false, onSelect: vi.fn() },
      ];
      await mount(
        <SoftkeyRail
          items={items}
          ariaLabel="MFD-A pages"
          railClassName="mfd-softkey-rail"
          itemClassName={() => 'mfd-key'}
          accentVar="--mfd-key-accent"
          activateOnArrow={false}
          homeEnd={false}
        />,
      );
      expect(tabs()[0].tabIndex).toBe(-1);
      expect(tabs()[1].tabIndex).toBe(0);
    });
  });
});
