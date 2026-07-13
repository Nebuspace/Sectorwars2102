/**
 * sidebarScreens — WO-UI1-CHROME-COMPLETE MFD-B slate + mid-panel fold
 * config. Pure data, no rendering needed.
 */
import { describe, it, expect } from 'vitest';
import { SIDEBAR_A, SIDEBAR_B, SIDEBAR_A_FOLDED } from './sidebarScreens';
import { MFD_PAGES } from './mfdRegistry';

describe('sidebarScreens — WO-UI1-CHROME-COMPLETE (ARIA absorbed into the teleprinter)', () => {
  it('MFD-B slate == [POS, COMM] -- no ARIA tab (canon §05 L578)', () => {
    expect(SIDEBAR_B.pageIds).toEqual(['nav-position', 'comms-crew']);
    expect(SIDEBAR_B.defaultPageId).toBe('nav-position');
    expect(SIDEBAR_B.systemLabel).toBe('MFD-B');
  });

  it('SIDEBAR_A is unchanged by this WO -- STAT / CRGO / QTM', () => {
    expect(SIDEBAR_A.pageIds).toEqual(['vessel-status', 'cargo', 'quantum-drive']);
  });

  it('SIDEBAR_A_FOLDED merges MFD-A + MFD-B into ONE rail at the 5-key softkey cap (canon: "5-key cap respected")', () => {
    expect(SIDEBAR_A_FOLDED.systemLabel).toBe('MFD-A');
    expect(SIDEBAR_A_FOLDED.pageIds).toEqual([
      'vessel-status', 'cargo', 'quantum-drive', 'nav-position', 'comms-crew',
    ]);
    expect(SIDEBAR_A_FOLDED.pageIds.length).toBe(5);
    // A distinct screenId from 'sidebar-a' -- see the config's own
    // doc-comment (MFDContext's REGISTER_SCREEN no-ops on a re-registered
    // screenId, which would otherwise freeze the fold's pageIds at the
    // unfolded 3).
    expect(SIDEBAR_A_FOLDED.screenId).toBe('sidebar-a-folded');
    expect(SIDEBAR_A_FOLDED.screenId).not.toBe(SIDEBAR_A.screenId);
  });

  it('every folded pageId has a real MFD_PAGES registry entry (no dangling id)', () => {
    for (const pageId of SIDEBAR_A_FOLDED.pageIds) {
      expect(MFD_PAGES[pageId]).toBeDefined();
    }
  });

  it('aria-terminal has no registry entry anywhere in the slate (fully retired)', () => {
    expect((MFD_PAGES as Record<string, unknown>)['aria-terminal']).toBeUndefined();
    expect(SIDEBAR_A.pageIds).not.toContain('aria-terminal');
    expect(SIDEBAR_B.pageIds).not.toContain('aria-terminal');
    expect(SIDEBAR_A_FOLDED.pageIds).not.toContain('aria-terminal');
  });
});
