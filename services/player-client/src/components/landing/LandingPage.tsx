import { useEffect, useRef, useState, type ReactNode } from 'react';
import './landing.css';

/**
 * LandingPage — the ratified neon "COLD START / IGNITION" pre-auth landing
 * (WO-LANDING-NEON). Ported from the design-of-record artifact at
 * audit/design-briefs/sw2102-landing-neon.html.
 *
 * Sub-parts (a)+(c) (scaffold + content) shipped the static "already lit"
 * structure. This pass (sub-parts b+d) wires the artifact's dynamism as
 * React effects: the living-galaxy starfield canvas, the cold-boot IGNITION
 * sequence (green phosphor boot log → ARIA greeting → flash → cyan reveal),
 * the hold-to-charge warp CTAs, the cursor-reactive ARIA feed tooltip, the
 * frame stroke-draw hero border, and the responsive/no-h-scroll pass.
 *
 * Deliberate scope decisions (flagged, not silent deviations from the
 * artifact):
 *  - The artifact's `.rv` scroll-reveal IntersectionObserver was NOT in this
 *    sub-part's enumerated bullet list and is intentionally left unbuilt —
 *    a fast-follow candidate, not a silent drop.
 *  - Boot shows once per browser (a WO addition beyond the artifact, which
 *    replays the full boot every load) via a `localStorage` seen-flag.
 *  - Hold-to-charge is a11y-widened beyond the artifact: the artifact's warp
 *    buttons only fire on a full 850ms mouse hold (no `onclick` at all, so
 *    keyboard Enter/Space did nothing). Here a plain `onClick` (which native
 *    <button> semantics fire for mouse click AND keyboard Enter/Space, and
 *    for a full hold that releases on-target) always registers — the hold
 *    animation is cosmetic enrichment layered on top, not a functional gate.
 *  - The artifact's `.feedline{opacity:0;animation:fin forwards}` combined
 *    with its `@media(prefers-reduced-motion:reduce){*{animation:none}}`
 *    rule leaves feed lines stuck invisible under reduced motion (the
 *    animation that would bring opacity to 1 never runs) — a latent bug in
 *    the design-of-record. Fixed here with an explicit reduced-motion
 *    opacity:1 override; flagging for the design-of-record's own record.
 */

interface LandingPageProps {
  onLogin: () => void;
  onRegister: () => void;
}

interface CoreLoopStep {
  fig: string;
  icon: string;
  title: string;
  description: string;
}

const CORE_LOOP: CoreLoopStep[] = [
  {
    fig: '01 · COMMERCE',
    icon: '💹',
    title: 'Trade',
    description:
      'Buy low, sell high across 5,300+ sectors and 12 port tiers. Corner the commodities before your rivals do.',
  },
  {
    fig: '02 · FRONTIER',
    icon: '🌀',
    title: 'Expand',
    description:
      "Raise warp gates to regions no pilot has ever seen. You don't just explore the galaxy — you grow it.",
  },
  {
    fig: '03 · GENESIS',
    icon: '🌍',
    title: 'Build',
    description:
      'Terraform empty space into living worlds. Colonize, industrialize, and fortify the planets you create.',
  },
  {
    fig: '04 · WARFARE',
    icon: '⚔️',
    title: 'Conquer',
    description:
      "Command fleets and siege rival holdings. Escape pods mean you're never wiped out — only forged sharper.",
  },
  {
    fig: '05 · DYNASTY',
    icon: '👑',
    title: 'Rule',
    description:
      'Climb 18 military ranks, forge crews into dynasties, and carve your name into a galaxy that keeps score.',
  },
];

interface SystemCard {
  icon: string;
  tag: string;
  title: string;
  description: ReactNode;
}

// SHIP SYSTEMS section — excludes the ARIA "feature" card, which has a
// distinct 2-column layout (chat mockup) and is hand-rendered below.
const SIGNATURE_SYSTEMS: SystemCard[] = [
  {
    icon: '🌀',
    tag: 'Expansion',
    title: 'Warp Jumpers & Gates',
    description:
      "Fly the galaxy's only jump-capable ship into uncharted dark — then sacrifice its hull to raise a warp gate that becomes part of everyone's map. Charge tolls on it. Defend it from siege.",
  },
  {
    icon: '🌍',
    tag: 'Creation',
    title: 'Genesis Devices',
    description:
      'Spin empty space into a brand-new living planet — the only way worlds enter this universe is players making them. Then colonize, defend, and grow what you built from nothing.',
  },
  {
    icon: '⚔️',
    tag: 'Tactical',
    title: 'Fleet Combat',
    description:
      "Deploy drones, siege planets, command fleets. Indestructible escape pods mean you're never truly wiped out.",
  },
  {
    icon: '👥',
    tag: 'Live',
    title: 'Real-Time Multiplayer',
    description:
      'Watch rivals streak across the galaxy live. Form teams, run joint Genesis ops, raise empires together.',
  },
  {
    icon: '📡',
    tag: 'Open',
    title: 'API-First by Design',
    description:
      "The entire game is a documented API — no web client required. Script your fleet, automate trade routes, or build your own cockpit. The door's open for bring-your-own-AI captains.",
  },
];

const COMMAND_CARDS: SystemCard[] = [
  {
    icon: '🏛️',
    tag: 'Ownership',
    title: 'Own the Port',
    description: (
      <>
        Seize a trade station — by purchase, economic squeeze, or open siege — then run it as a
        business. Set the tariffs, docking fees, and price lever, and watch the treasury fill
        while rivals dock at <em>your</em> rates.
      </>
    ),
  },
  {
    icon: '🏰',
    tag: 'Colony',
    title: 'Raise a Citadel',
    description:
      'Grow a frontier outpost into a 200,000-strong Planetary Capital across five levels — and bank your fortune in a safe vault that survives even if the colony above it is glassed.',
  },
  {
    icon: '🌱',
    tag: 'Worlds',
    title: 'Terraform the Dead',
    description: (
      <>
        Rebuild a barren rock into a paradise on a five-stage habitability ladder — a slow,
        enormous fortune. Distinct from Genesis: one <em>makes</em> new worlds, this one{' '}
        <em>perfects</em> them.
      </>
    ),
  },
  {
    icon: '🌌',
    tag: 'Discovery',
    title: 'The Map Has Secrets',
    description:
      "Stumble into Bubbles, Backdoors, and a Warp Sink you can enter but never leave — each with a name you won't forget. And the Lost Worlds off the grid? Only a Warp Jumper's blind Quantum Jump reaches those.",
  },
  {
    icon: '⛏️',
    tag: 'Resources',
    title: 'Work the Deep Rock',
    description:
      'Fit a mining laser to asteroid fields and nebulae, file your claim, and gamble the deep sectors for a quantum-shard strike — the rare fuel that powers warp-gate construction.',
  },
  {
    icon: '🗳️',
    tag: 'Politics',
    title: 'Rule a Region',
    description:
      'Form a team with a shared treasury and coordinated fleets, then own a whole region — hold elections, pass policies, and sign binding treaties with the powers next door.',
  },
];

const EMERGENT_CARDS: SystemCard[] = [
  {
    icon: '🎨',
    tag: 'Reputation',
    title: 'Your Name Has a Color',
    description: (
      <>
        Standing is earned by what you do, never by a quest. Cross the line and your name burns{' '}
        <span className="landing-text-magenta">red</span> for every player to see — with a
        Federation bounty auto-posted on your head. Play the hero and it glows{' '}
        <span className="landing-text-cyan">Legendary cyan</span>.
      </>
    ),
  },
  {
    icon: '🛡️',
    tag: 'Survival',
    title: "You're Never Truly Out",
    description:
      "Your escape pod is indestructible — it can't even be targeted fresh — and your citadel vault outlives the colony's destruction. Rock bottom is a setback, never a wipe.",
  },
  {
    icon: '☠️',
    tag: 'Threat',
    title: 'Pirates Spread Like a Virus',
    description:
      'Ignore an infestation and it evolves — a camp becomes an outpost becomes a stronghold with named Lords. Clear one and hold the ground, and the damage you dealt is still there when you come back.',
  },
  {
    icon: '📻',
    tag: 'Presence',
    title: 'Leave a Message in the Void',
    description:
      'Drop a text beacon in any sector for whoever wanders by next — a warning, a boast, a treasure hint. Set it read-once and it dies with the first reader. The galaxy slowly fills with player-written history.',
  },
  {
    icon: '💬',
    tag: 'Bargaining',
    title: 'Haggle a Captain Down',
    description:
      "Prices aren't fixed. Talk a trader down across a real back-and-forth — and every station personality drives a different bargain, from the hard-nosed Frontier runner to the velvet-gloved luxury broker.",
  },
];

interface StatItem {
  value: ReactNode;
  label: string;
}

const GALAXY_STATS: StatItem[] = [
  {
    value: (
      <>
        5,300<span className="landing-met-u">+</span>
      </>
    ),
    label: 'Sectors to Chart',
  },
  { value: '∞', label: 'Genesis Worlds' },
  {
    value: (
      <>
        1<span className="landing-met-u">st</span>
      </>
    ),
    label: 'Learning AI Wingman',
  },
  { value: '24/7', label: 'Live Persistent Galaxy' },
];

interface FeedLine {
  tone: '' | 'sys' | 'warn';
  tag: string;
  text: string;
}

// The artifact's full live-feed cycle (its 7 `push()` payloads). The first
// four seed the initial render (before any interval fires); the interval
// effect below cycles through all 7, wrapping, once live.
const ARIA_FEED_LINES: FeedLine[] = [
  { tone: '', tag: 'ARIA>', text: 'online. plotting your first loop, Commander.' },
  { tone: 'sys', tag: 'SCAN>', text: 'sector 271 · 6 bodies · hazard band charted' },
  { tone: '', tag: 'ARIA>', text: 'route to Frontier Hub Lyra optimized (+1,204¢)' },
  { tone: 'warn', tag: 'ALERT>', text: 'hostile fleet in range — rerouting' },
  { tone: '', tag: 'ARIA>', text: 'genesis window opens in 3 turns' },
  { tone: 'sys', tag: 'LINK>', text: 'warp gate 47 → new region unlocked' },
  { tone: '', tag: 'ARIA>', text: 'market swing predicted: ORE +18% (2 turns)' },
];

const ARIA_FEED_MAX_VISIBLE = 7;

interface ThenNowLine {
  marker: string;
  content: ReactNode;
}

const THEN_LINES: ThenNowLine[] = [
  { marker: '—', content: 'Turn-by-turn, played in the dark between logins' },
  { marker: '—', content: 'A fixed map, memorized one sector at a time' },
  { marker: '—', content: 'Alone with a notepad and a gut feeling' },
  { marker: '—', content: 'Green phosphor on a dial-up terminal' },
];

const NOW_LINES: ThenNowLine[] = [
  { marker: '●', content: 'Real-time and always-on — watch rivals streak past live' },
  {
    marker: '●',
    content: (
      <>
        An <strong className="landing-tnl-em">expanding</strong> universe you and other players
        grow
      </>
    ),
  },
  {
    marker: '●',
    content: (
      <>
        ARIA — an AI wingman that learns <strong className="landing-tnl-em">your</strong> playbook
      </>
    ),
  },
  { marker: '●', content: 'A living neon cockpit, right in your browser' },
];

// ---------------------------------------------------------------------------
// Boot / ignition data + small sync helpers
// ---------------------------------------------------------------------------

const LANDING_BOOT_SEEN_KEY = 'sw2102-landing-boot-seen';
const GALAXY_SEED = 20250712;
const IGNITE_FLASH_MS = 1100;
const IGNITE_HIDE_MS = 700;
const AUTO_WAKE_MS = 2600;
const BOOT_CHAR_MS = 14;
const BOOT_LINE_PAUSE_MS = 120;
const ARIA_GREET_CHAR_MS = 34;

type BootStage = 'typing' | 'fading' | 'hidden';

interface BootLine {
  dim: boolean;
  text: string;
}

const BOOT_LINES: BootLine[] = [
  { dim: true, text: '> CARRIER DETECTED · HANDSHAKE @ 2400 BAUD' },
  { dim: true, text: '> LINK ESTABLISHED · NODE SW-2102' },
  { dim: false, text: '> POST … MEMORY OK · WARP CORE OK · SCANNERS OK' },
  { dim: true, text: '> LAST SESSION: ████ · [ RECORD DEGRADED ]' },
  { dim: false, text: '> TURNS REMAINING: ∞' },
  { dim: true, text: '> WAKING SHIP INTELLIGENCE …' },
];

const ARIA_BOOT_GREETING = 'ARIA> You’re back. It’s been a while.';

const ARIA_CURSOR_QUIPS: readonly string[] = [
  'scanning …',
  'profitable vector',
  'she’s watching your line',
  'frontier bearing 047',
  'contact — live pilot',
  'warp lane stable',
];

function prefersReducedMotionSync(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

function hasSeenLandingBootSync(): boolean {
  try {
    return typeof window !== 'undefined' && window.localStorage.getItem(LANDING_BOOT_SEEN_KEY) === '1';
  } catch {
    // localStorage can throw (private mode / disabled) -- treat as unseen.
    return false;
  }
}

function markLandingBootSeen(): void {
  try {
    window.localStorage.setItem(LANDING_BOOT_SEEN_KEY, '1');
  } catch {
    // localStorage can throw (private mode / disabled) -- non-fatal, mirrors
    // SettingsContext's convention for the same guard.
  }
}

/** Tiny deterministic LCG PRNG — mirrors the design-of-record artifact's
 * `S(x)` generator (identical seed + recurrence) so the star/node layout is
 * reproducible across resizes rather than re-randomizing every time. */
function makeGalaxyRng(seed: number): () => number {
  let x = seed;
  return () => {
    x = (x * 1103515245 + 12345) & 0x7fffffff;
    return x / 0x7fffffff;
  };
}

/** Live prefers-reduced-motion tracking — mirrors SolarSystemViewscreen's /
 * Annunciator's established useState+matchMedia pattern (duplicated locally
 * per that convention: extracting to a shared hook would mean editing files
 * outside this sub-part's owned two-file boundary). */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotionSync);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  return reduced;
}

/** Two of the plate's four bezel corners are literal DOM nodes (the other
 * two come from ::before/::after on .landing-plate) — mirrors the
 * artifact's .c1/.c2 spans. */
function PlateCorners() {
  return (
    <>
      <span className="landing-plate-c1" />
      <span className="landing-plate-c2" />
    </>
  );
}

function SystemCardEl({ card }: { card: SystemCard }) {
  return (
    <li className="landing-plate landing-card">
      <PlateCorners />
      <div className="landing-card-top">
        <span className="landing-card-ic">{card.icon}</span>
        <span className="landing-card-tag">{card.tag}</span>
      </div>
      <h3>{card.title}</h3>
      <p>{card.description}</p>
    </li>
  );
}

/** HOLD TO JUMP IN / HOLD TO ENLIST warp CTA. Mousedown/up drive a purely
 * cosmetic charge-fill (direct DOM style writes, matching the artifact's own
 * imperative approach — no React re-render needed for a 60fps fill sweep).
 * `onClick` is the single source of truth for activation: native <button>
 * semantics fire it for a mouse click of ANY duration (including a full
 * charge-and-release) AND for keyboard Enter/Space — see the file-header
 * a11y note for why this widens past the artifact's mouse-only hold gate. */
function WarpButton({
  onActivate,
  className,
  children,
}: {
  onActivate: () => void;
  className: string;
  children: ReactNode;
}) {
  const fillRef = useRef<HTMLSpanElement>(null);

  const startCharge = () => {
    const fill = fillRef.current;
    if (!fill) return;
    fill.style.transitionDuration = '850ms';
    fill.style.transform = 'scaleX(1)';
  };

  const endCharge = () => {
    const fill = fillRef.current;
    if (!fill) return;
    fill.style.transitionDuration = '200ms';
    fill.style.transform = 'scaleX(0)';
  };

  return (
    <button
      className={className}
      onClick={onActivate}
      onMouseDown={startCharge}
      onMouseUp={endCharge}
      onMouseLeave={endCharge}
      onTouchStart={startCharge}
      onTouchEnd={endCharge}
    >
      <span className="landing-warp-fill" ref={fillRef} aria-hidden="true" />
      <span className="landing-warp-label">{children}</span>
    </button>
  );
}

interface BootOverlayProps {
  stage: BootStage;
  bootRevealed: string[];
  ariaGreetRevealed: string;
  showWake: boolean;
  onSkip: () => void;
  onWake: () => void;
}

/** The green-phosphor "COLD START" boot overlay — first-visit only (gated by
 * the localStorage seen-flag) and skippable at any point. Not marked
 * aria-hidden: it holds the only two focusable controls (SKIP/WAKE) while
 * shown, and hiding a container from AT while it holds focusable children
 * is its own a11y bug. Pixel gate FIX 1: modal semantics (role="dialog"
 * aria-modal) + initial focus on SKIP on mount -- lightweight, not a full
 * focus trap, since the existing auto-wake (2600ms) + any-keydown-skips
 * already provide an escape hatch; focus restoration on dismiss lives in
 * the parent (it owns the "sensible landing element" to return to). */
function BootOverlay({ stage, bootRevealed, ariaGreetRevealed, showWake, onSkip, onWake }: BootOverlayProps) {
  const skipRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    skipRef.current?.focus();
  }, []);

  return (
    <div
      className={`landing-boot${stage === 'fading' ? ' landing-boot-gone' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label="Cold-start boot sequence"
    >
      <button className="landing-skip" onClick={onSkip} ref={skipRef}>
        SKIP INTRO ▸
      </button>
      <div className="landing-boot-crt" aria-hidden="true" />
      <div className="landing-boot-inner">
        <div>
          {BOOT_LINES.map((line, i) => (
            <div key={i} className={`landing-boot-line${line.dim ? ' dim' : ''}`}>
              {bootRevealed[i]}
            </div>
          ))}
        </div>
        {ariaGreetRevealed && (
          <div className="landing-boot-aria">
            {ariaGreetRevealed}
            <span className="landing-caret" aria-hidden="true" />
          </div>
        )}
        {showWake && (
          <button className="landing-wake" onClick={onWake}>
            ⏻ WAKE HER
          </button>
        )}
      </div>
    </div>
  );
}

export default function LandingPage({ onLogin, onRegister }: LandingPageProps) {
  const galaxyCanvasRef = useRef<HTMLCanvasElement>(null);
  const loopSectionRef = useRef<HTMLElement>(null);
  const heroRef = useRef<HTMLElement>(null);
  const cursorNoteRef = useRef<HTMLDivElement>(null);
  // Pixel gate FIX 1: the boot overlay's focus-restoration target on
  // dismiss -- the topbar's first CTA (Login), always present/visible.
  const topbarLoginRef = useRef<HTMLButtonElement>(null);

  const reducedMotion = useReducedMotion();

  const [bootStage, setBootStage] = useState<BootStage>(() =>
    prefersReducedMotionSync() || hasSeenLandingBootSync() ? 'hidden' : 'typing'
  );
  const [bootRevealed, setBootRevealed] = useState<string[]>(() => BOOT_LINES.map(() => ''));
  const [ariaGreetRevealed, setAriaGreetRevealed] = useState('');
  const [showWake, setShowWake] = useState(false);
  const [flashActive, setFlashActive] = useState(false);
  const [feedLines, setFeedLines] = useState<Array<FeedLine & { id: number }>>(() =>
    ARIA_FEED_LINES.slice(0, 4).map((line, id) => ({ ...line, id }))
  );

  const ignitedRef = useRef(bootStage === 'hidden');
  const skippedIgnitionAnimRef = useRef(bootStage === 'hidden');
  const autoWakeTimerIdRef = useRef<number | null>(null);
  const pendingTimersRef = useRef<Set<number>>(new Set());
  const shockwaveRef = useRef(0);
  const feedCycleIndexRef = useRef(4);

  const isLive = bootStage !== 'typing';
  const frameDrawClass = !isLive ? '' : skippedIgnitionAnimRef.current ? 'landing-frame-instant' : 'landing-frame-drawn';

  const scrollToLoop = () => {
    loopSectionRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // Unmount safety net: clears every timer scheduled via trackTimeout below,
  // regardless of which effect/handler created it.
  useEffect(() => {
    return () => {
      pendingTimersRef.current.forEach((id) => window.clearTimeout(id));
      pendingTimersRef.current.clear();
    };
  }, []);

  // Pixel gate FIX 1 (continued): BootOverlay focuses its own SKIP button on
  // mount; this half restores focus once it's dismissed (bootStage
  // transitions to 'hidden' from either 'typing' -- SKIP/instant path -- or
  // 'fading' -- the animated ignite path) so a keyboard/SR user isn't left
  // with focus on a node that just left the document.
  const prevBootStageRef = useRef(bootStage);
  useEffect(() => {
    const prevStage = prevBootStageRef.current;
    prevBootStageRef.current = bootStage;
    if (prevStage !== 'hidden' && bootStage === 'hidden') {
      topbarLoginRef.current?.focus();
    }
  }, [bootStage]);

  function trackTimeout(fn: () => void, ms: number): number {
    const id = window.setTimeout(() => {
      pendingTimersRef.current.delete(id);
      fn();
    }, ms);
    pendingTimersRef.current.add(id);
    return id;
  }

  function ignite(skip?: boolean) {
    if (ignitedRef.current) return;
    ignitedRef.current = true;
    if (autoWakeTimerIdRef.current !== null) {
      window.clearTimeout(autoWakeTimerIdRef.current);
      pendingTimersRef.current.delete(autoWakeTimerIdRef.current);
      autoWakeTimerIdRef.current = null;
    }
    markLandingBootSeen();

    if (reducedMotion || skip) {
      skippedIgnitionAnimRef.current = true;
      setBootStage('hidden');
      return;
    }

    skippedIgnitionAnimRef.current = false;
    shockwaveRef.current = 1;
    setFlashActive(true);
    setBootStage('fading');
    trackTimeout(() => setFlashActive(false), IGNITE_FLASH_MS);
    trackTimeout(() => setBootStage('hidden'), IGNITE_HIDE_MS);
  }

  // ---------------------------------------------------------------------------
  // Boot typewriter: green-phosphor log lines -> ARIA greeting -> WAKE button
  // (+ an auto-wake fallback) -> any early keydown/wheel skips straight to
  // ignite(). Only runs at all on a first-ever, non-reduced-motion visit
  // (bootStage starts 'typing' only in that case).
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (bootStage !== 'typing') return undefined;

    let cancelled = false;
    const localTimerIds: number[] = [];
    const after = (fn: () => void, ms: number) => {
      const id = trackTimeout(() => {
        if (!cancelled) fn();
      }, ms);
      localTimerIds.push(id);
    };

    function typeBootLine(lineIndex: number) {
      if (lineIndex >= BOOT_LINES.length) {
        after(() => typeGreeting(0), 0);
        return;
      }
      const text = BOOT_LINES[lineIndex].text;
      const tick = (charIndex: number) => {
        setBootRevealed((prev) => {
          const next = [...prev];
          next[lineIndex] = text.slice(0, charIndex);
          return next;
        });
        if (charIndex < text.length) after(() => tick(charIndex + 1), BOOT_CHAR_MS);
        else after(() => typeBootLine(lineIndex + 1), BOOT_LINE_PAUSE_MS);
      };
      tick(0);
    }

    function typeGreeting(charIndex: number) {
      setAriaGreetRevealed(ARIA_BOOT_GREETING.slice(0, charIndex));
      if (charIndex < ARIA_BOOT_GREETING.length) {
        after(() => typeGreeting(charIndex + 1), ARIA_GREET_CHAR_MS);
      } else {
        setShowWake(true);
        const id = trackTimeout(() => {
          if (!cancelled) ignite();
        }, AUTO_WAKE_MS);
        localTimerIds.push(id);
        autoWakeTimerIdRef.current = id;
      }
    }

    typeBootLine(0);

    function handleEarlyKeydown(e: KeyboardEvent) {
      if (e.key !== 'Tab') ignite();
    }
    function handleEarlyWheel() {
      ignite();
    }
    window.addEventListener('keydown', handleEarlyKeydown);
    window.addEventListener('wheel', handleEarlyWheel, { passive: true });

    return () => {
      cancelled = true;
      localTimerIds.forEach((id) => {
        window.clearTimeout(id);
        pendingTimersRef.current.delete(id);
      });
      window.removeEventListener('keydown', handleEarlyKeydown);
      window.removeEventListener('wheel', handleEarlyWheel);
    };
    // `ignite`/`trackTimeout` are plain function declarations re-created each
    // render, but both only close over refs/setters that are themselves
    // stable across renders -- safe to key this effect on bootStage alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootStage]);

  // ---------------------------------------------------------------------------
  // Living-galaxy starfield canvas. Runs for the component's full lifetime
  // whenever motion isn't reduced (matches the artifact: it starts drawing
  // immediately, invisible behind the boot overlay, so it's already moving
  // the instant it's revealed). Reduced-motion SKIPS the RAF loop entirely
  // rather than running a motionless one -- no continuous frame work for a
  // static graphic. Canvas visibility itself is a CSS opacity class tied to
  // `isLive`, not this effect.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (reducedMotion) return undefined;
    const canvas = galaxyCanvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    if (!ctx) return undefined;

    const rng = makeGalaxyRng(GALAXY_SEED);
    const NODE_COLORS = ['#22d3ee', '#b06bff', '#ffb020'];
    let width = 0;
    let height = 0;
    let stars: Array<{ x: number; y: number; z: number; r: number; tw: number; dx: number }> = [];
    let nodes: Array<{ x: number; y: number; c: number; hub?: boolean }> = [];
    let rafId = 0;

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas!.clientWidth;
      height = canvas!.clientHeight;
      canvas!.width = width * dpr;
      canvas!.height = height * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      stars = [];
      const starCount = Math.min(230, Math.floor((width * height) / 6800));
      for (let i = 0; i < starCount; i++) {
        stars.push({
          x: rng() * width,
          y: rng() * height,
          z: rng(),
          r: rng() * 1.3 + 0.2,
          tw: rng() * 6.28,
          dx: (rng() - 0.5) * 0.04,
        });
      }

      nodes = [];
      const cx = width * 0.62;
      const cy = height * 0.5;
      const R = Math.min(width, height) * 0.34;
      const colorCycle = [0, 0, 0, 1, 2, 0, 2];
      for (let k = 0; k < 7; k++) {
        const a = (k / 7) * 6.28 - 1.1;
        nodes.push({
          x: cx + Math.cos(a) * R * (0.7 + rng() * 0.5),
          y: cy + Math.sin(a) * R * (0.55 + rng() * 0.5),
          c: colorCycle[k],
        });
      }
      nodes.push({ x: cx, y: cy, c: 0, hub: true });
    }

    function draw(ts: number) {
      ctx!.clearRect(0, 0, width, height);
      for (const s of stars) {
        s.x += s.dx * (0.4 + s.z);
        if (s.x < 0) s.x = width;
        if (s.x > width) s.x = 0;
        const tw = 0.55 + 0.45 * Math.sin(ts / 900 + s.tw);
        ctx!.globalAlpha = (0.22 + 0.6 * s.z) * tw;
        ctx!.fillStyle = s.z > 0.85 ? '#bfe9ff' : '#7fa6cf';
        ctx!.beginPath();
        ctx!.arc(s.x, s.y, s.r * (0.6 + s.z), 0, 6.28);
        ctx!.fill();
      }
      ctx!.globalAlpha = 1;
      const hub = nodes[nodes.length - 1];
      for (let k = 0; k < nodes.length - 1; k++) {
        const nn = nodes[k];
        ctx!.strokeStyle = 'rgba(34,211,238,.15)';
        ctx!.lineWidth = 1;
        ctx!.beginPath();
        ctx!.moveTo(hub.x, hub.y);
        ctx!.lineTo(nn.x, nn.y);
        ctx!.stroke();
      }
      for (let k = 0; k < nodes.length; k++) {
        const nn = nodes[k];
        const color = nn.hub ? '#ffb020' : NODE_COLORS[nn.c];
        const pulse = 0.6 + 0.4 * Math.sin(ts / 700 + k);
        ctx!.fillStyle = color;
        ctx!.shadowColor = color;
        ctx!.shadowBlur = (nn.hub ? 16 : 9) * pulse;
        ctx!.beginPath();
        ctx!.arc(nn.x, nn.y, nn.hub ? 6 : 4, 0, 6.28);
        ctx!.fill();
        ctx!.shadowBlur = 0;
      }
      if (shockwaveRef.current > 0) {
        const sw = shockwaveRef.current;
        ctx!.strokeStyle = `rgba(139,240,255,${Math.max(0, sw)})`;
        ctx!.lineWidth = 2;
        ctx!.beginPath();
        ctx!.arc(hub.x, hub.y, (1 - sw) * Math.max(width, height) * 0.9, 0, 6.28);
        ctx!.stroke();
        shockwaveRef.current -= 0.02;
      }
      rafId = requestAnimationFrame(draw);
    }

    resize();
    rafId = requestAnimationFrame(draw);
    window.addEventListener('resize', resize);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener('resize', resize);
    };
  }, [reducedMotion]);

  // ---------------------------------------------------------------------------
  // Live ARIA feed: cycles through the full 7-line script once live, capped
  // to the last 7 visible lines (matches the artifact's feed.children>7
  // trim). Reduced-motion keeps the static 4-line seed (no interval).
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!isLive || reducedMotion) return undefined;
    const id = window.setInterval(() => {
      setFeedLines((prev) => {
        const nextId = feedCycleIndexRef.current;
        const line = ARIA_FEED_LINES[nextId % ARIA_FEED_LINES.length];
        feedCycleIndexRef.current += 1;
        const next = [...prev, { ...line, id: nextId }];
        return next.length > ARIA_FEED_MAX_VISIBLE ? next.slice(next.length - ARIA_FEED_MAX_VISIBLE) : next;
      });
    }, 2600);
    return () => window.clearInterval(id);
  }, [isLive, reducedMotion]);

  // ---------------------------------------------------------------------------
  // Cursor-reactive ARIA tooltip, bound to the hero once live. Direct DOM
  // style writes (matches the artifact) -- a per-pixel tooltip position has
  // no business going through React state/re-render.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!isLive || reducedMotion) return undefined;
    const hero = heroRef.current;
    const note = cursorNoteRef.current;
    if (!hero || !note) return undefined;

    let lastMoveAt = 0;
    let hideTimerId: number | null = null;

    function handleMouseMove(e: MouseEvent) {
      const now = Date.now();
      if (now - lastMoveAt < 90) return;
      lastMoveAt = now;
      note!.style.left = `${e.clientX + 16}px`;
      note!.style.top = `${e.clientY + 14}px`;
      note!.style.opacity = '1';
      if (Math.random() < 0.06) {
        const quip = ARIA_CURSOR_QUIPS[Math.floor(Math.random() * ARIA_CURSOR_QUIPS.length)];
        note!.textContent = `ARIA: ${quip}`;
      }
      if (!note!.textContent) note!.textContent = 'ARIA: scanning …';
      if (hideTimerId !== null) window.clearTimeout(hideTimerId);
      hideTimerId = window.setTimeout(() => {
        note!.style.opacity = '0';
      }, 1400);
    }

    function handleMouseLeave() {
      note!.style.opacity = '0';
    }

    hero.addEventListener('mousemove', handleMouseMove);
    hero.addEventListener('mouseleave', handleMouseLeave);

    return () => {
      hero.removeEventListener('mousemove', handleMouseMove);
      hero.removeEventListener('mouseleave', handleMouseLeave);
      if (hideTimerId !== null) window.clearTimeout(hideTimerId);
    };
  }, [isLive, reducedMotion]);

  return (
    <div className="landing-root">
      {bootStage !== 'hidden' && (
        <BootOverlay
          stage={bootStage}
          bootRevealed={bootRevealed}
          ariaGreetRevealed={ariaGreetRevealed}
          showWake={showWake}
          onSkip={() => ignite(true)}
          onWake={() => ignite()}
        />
      )}
      {flashActive && <div className="landing-flash" aria-hidden="true" />}

      <nav className="landing-topbar">
        <div className="landing-wrap landing-topbar-row">
          <div className="landing-brand">
            <b>SECTOR&nbsp;WARS</b>
            <span className="landing-yr">2102</span>
          </div>
          <div className="landing-navr">
            <div className="landing-chip">
              <span className="landing-dot" />
              UNIVERSE ONLINE
            </div>
            <button className="landing-btn" onClick={onLogin} ref={topbarLoginRef}>
              Login
            </button>
            <button className="landing-btn landing-btn-primary" onClick={onRegister}>
              Join Now
            </button>
          </div>
        </div>
      </nav>

      <main>
      <header className="landing-hero" ref={heroRef}>
        <canvas
          ref={galaxyCanvasRef}
          className={`landing-galaxy-canvas${isLive ? ' landing-galaxy-canvas-live' : ''}`}
          aria-hidden="true"
        />
        <svg className="landing-frame" preserveAspectRatio="none" aria-hidden="true">
          <rect x="1" y="1" width="99%" height="99%" rx="4" pathLength={1} className={`landing-frame-rect ${frameDrawClass}`} />
        </svg>
        <div className="landing-wrap landing-hero-grid">
          <div className="landing-hero-l">
            <div className="landing-hero-fig">
              <span className="landing-ret" /> FIG.01 · NEURAL LINK · ARIA CONSCIOUSNESS ACTIVE
            </div>
            <h1 className="landing-htitle">
              COMMAND THE
              <span className="landing-htitle-g">GALAXY</span>
            </h1>
            <p className="landing-hsub">
              A living universe of 5,300+ sectors. Trade, terraform, and wage war beside{' '}
              <b>ARIA</b> — the first AI companion that learns how <i>you</i> play. The next
              chapter of the game that defined deep space.
            </p>
            <div className="landing-ctas">
              <WarpButton onActivate={onRegister} className="landing-warp">
                ⚡ HOLD TO JUMP IN
              </WarpButton>
              <button className="landing-ghost" onClick={scrollToLoop}>
                EXPLORE SYSTEMS
              </button>
            </div>
          </div>
          <div className="landing-plate landing-mfd">
            <PlateCorners />
            <div className="landing-mfd-h">
              <span>
                <b>◈</b> ARIA · LIVE FEED
              </span>
              <span className="landing-fig-faint">PL-2102/ARIA</span>
            </div>
            <div className="landing-mfd-body" aria-live="polite" aria-label="Game feed">
              {feedLines.map((line) => (
                <div key={line.id} className={`landing-feedline ${line.tone}`}>
                  <span className="landing-feedline-w">{line.tag}</span> {line.text}
                </div>
              ))}
            </div>
          </div>
        </div>
      </header>

      <div className="landing-band">
        <dl className="landing-wrap landing-band-row">
          {GALAXY_STATS.map((stat) => (
            <div className="landing-met" key={stat.label}>
              <dt className="landing-met-l">{stat.label}</dt>
              <dd className="landing-met-n">{stat.value}</dd>
            </div>
          ))}
        </dl>
      </div>

      <section className="landing-blk" ref={loopSectionRef}>
        <div className="landing-wrap">
          <div className="landing-sh">
            <span className="landing-eyebrow">FIG.02 · THE CORE LOOP</span>
            <h2>One more jump. Then one more.</h2>
            <p>
              Every run leaves you richer, deadlier, and harder to ignore. The galaxy never resets
              — it remembers what you did and who you crossed.
            </p>
          </div>
          <ul className="landing-loops" aria-label="Core loop steps">
            {CORE_LOOP.map((step, i) => (
              <li className="landing-plate landing-loop" key={step.title}>
                <PlateCorners />
                <span className="landing-fig-faint landing-loop-fig">{step.fig}</span>
                <span className="landing-loop-ic">{step.icon}</span>
                <h4>{step.title}</h4>
                <p>{step.description}</p>
                {i < CORE_LOOP.length - 1 && <span className="landing-arw" aria-hidden="true">▸</span>}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="landing-blk">
        <div className="landing-wrap">
          <div className="landing-sh">
            <span className="landing-eyebrow">FIG.03 · SHIP SYSTEMS</span>
            <h2>Not another space game. A universe with a mind.</h2>
            <p>
              Cutting-edge AI, player-built expansion, and real-time strategy — every system
              engineered for depth.
            </p>
          </div>
          <ul className="landing-feat" aria-label="Signature systems">
            <li className="landing-plate landing-card landing-card-feature">
              <PlateCorners />
              <div>
                <div className="landing-card-top">
                  <span className="landing-card-ic">🤖</span>
                  <span className="landing-card-tag">Machine Learning</span>
                </div>
                <h3>ARIA — your learning wingman</h3>
                <p>
                  The galaxy's first AI companion that studies your trading style, forecasts
                  market swings, and plots routes in real time. She doesn't just assist — she
                  adapts to you, and grows sharper every jump.
                </p>
              </div>
              <div className="landing-amini">
                <div className="landing-amini-ln">
                  <span className="landing-amini-p">{'ARIA>'}</span>{' '}
                  <span className="landing-amini-a">
                    Profitable loop — Sol → Sector 47, +2,140¢/turn.
                  </span>
                </div>
                <div className="landing-amini-ln">
                  <span className="landing-amini-q">{'you> is it safe?'}</span>
                </div>
                <div className="landing-amini-ln">
                  <span className="landing-amini-p">{'ARIA>'}</span>{' '}
                  <span className="landing-amini-a">
                    Two hostiles in range. Reroute via 312 costs 1 turn — take it.
                  </span>
                </div>
                <div className="landing-amini-ln">
                  <span className="landing-amini-p">{'ARIA>'}</span>{' '}
                  <span className="landing-amini-a">Standing by, Commander.</span>
                </div>
              </div>
            </li>
            {SIGNATURE_SYSTEMS.map((card) => (
              <SystemCardEl card={card} key={card.title} />
            ))}
          </ul>
        </div>
      </section>

      <section className="landing-blk">
        <div className="landing-wrap">
          <div className="landing-sh">
            <span className="landing-eyebrow">FIG.04 · WHAT YOU COMMAND</span>
            <h2>Everything here is yours to seize.</h2>
            <p>
              Ports, worlds, whole regions of space — this isn't a game you play <em>through</em>.
              It's a galaxy you take pieces of, and hold.
            </p>
          </div>
          <ul className="landing-feat" aria-label="What you command">
            {COMMAND_CARDS.map((card) => (
              <SystemCardEl card={card} key={card.title} />
            ))}
          </ul>
        </div>
      </section>

      <section className="landing-blk">
        <div className="landing-wrap">
          <div className="landing-sh">
            <span className="landing-eyebrow">FIG.05 · EMERGENT</span>
            <h2>There are no quests. Only consequences.</h2>
            <p>Nothing here hands you a mission. The galaxy simply remembers what you did — and reacts.</p>
          </div>
          <ul className="landing-feat" aria-label="Emergent systems">
            {EMERGENT_CARDS.map((card) => (
              <SystemCardEl card={card} key={card.title} />
            ))}
          </ul>
        </div>
      </section>

      <section className="landing-blk landing-reborn">
        <div className="landing-wrap">
          <div className="landing-sh">
            <span className="landing-eyebrow">FIG.06 · REBORN FOR 2102</span>
            <h2>You've felt this pull before.</h2>
            <p>
              A galaxy you couldn't stop charting — <em>turns rationed</em>, rivals scheming in
              the dark, a universe that felt bigger than the <em>modem</em> carrying it. That itch
              never really left. We rebuilt it for now.
            </p>
          </div>
          <div className="landing-plate landing-thennow">
            <PlateCorners />
            <div className="landing-tn landing-tn-then">
              <div className="landing-tn-hd">◄ THE CLASSIC YOU REMEMBER</div>
              <ul className="landing-tnl-list" aria-label="THEN">
                {THEN_LINES.map((line, i) => (
                  <li className="landing-tnl" key={i}>
                    <span className="landing-tnl-m" aria-hidden="true">{line.marker}</span>
                    <span>{line.content}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="landing-tn landing-tn-now">
              <div className="landing-tn-hd">SECTOR WARS 2102 ►</div>
              <ul className="landing-tnl-list" aria-label="NOW 2102">
                {NOW_LINES.map((line, i) => (
                  <li className="landing-tnl" key={i}>
                    <span className="landing-tnl-m" aria-hidden="true">{line.marker}</span>
                    <span>{line.content}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section className="landing-final">
        <div className="landing-wrap">
          <div className="landing-plate landing-console">
            <PlateCorners />
            <span className="landing-eyebrow">ENLISTMENT OPEN</span>
            <h2>Your sector is waiting, Captain.</h2>
            <p>
              Boot the cockpit. Meet ARIA. Chart the frontier before anyone else does. Your first
              100 turns are on us.
            </p>
            <WarpButton onActivate={onRegister} className="landing-warp">
              ⚡ HOLD TO ENLIST
            </WarpButton>
          </div>
        </div>
      </section>

      <div className="landing-wrap landing-easter-egg">
        ⌁ intercepted transmission — word is there's an old orange cat who's prowled the Callisto
        shipyard longer than anyone can explain. Be kind on your first login; it tends to pay off.
      </div>
      </main>

      <footer className="landing-footer">
        <div className="landing-wrap landing-footer-row">
          <span>SECTOR WARS 2102 · A living space-trading universe</span>
          <span>
            ARIA CORE <span className="landing-footer-online">● ONLINE</span> ·{' '}
            <a href="#">Docs</a> · <a href="#">Community</a>
          </span>
        </div>
      </footer>

      <div className="landing-cursor-note" ref={cursorNoteRef} aria-hidden="true" />
    </div>
  );
}
