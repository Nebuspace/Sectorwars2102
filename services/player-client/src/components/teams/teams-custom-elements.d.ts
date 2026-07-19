// MissionPlanner.tsx and TeamManager.tsx pair <label>/<value> as a custom
// tag, styled by element selector (mission-planner.css, team-manager.css:
// `.info-item value`, `.reward-item value`, `.stat-item value`,
// `.contribution-item value`). This declares <value> as a valid intrinsic
// so those files type-check without rewriting the tag (which would silently
// break the CSS selectors targeting it).
//
// NOTE: this augmentation is GLOBAL to the compile (module augmentation
// ignores file location) — <value> type-checks everywhere, not just teams/.
// It exists ONLY for the legacy teams/ pattern above; do not use <value> in
// new code. Remove this file when WO-PUX-FE-ORPHANS dispositions these
// components.
//
// The `import type` below is required to make this file a module so that
// `declare module 'react'` MERGES into react's existing types instead of
// replacing them outright.
import type { DetailedHTMLProps, HTMLAttributes } from 'react';

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      value: DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement>;
    }
  }
}
