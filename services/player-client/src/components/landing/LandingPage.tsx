import { useRef, type ReactNode } from 'react';
import './landing.css';

/**
 * LandingPage — the ratified neon "COLD START / IGNITION" pre-auth landing
 * (WO-LANDING-NEON sub-parts a+c). Ported verbatim from the design-of-record
 * artifact at audit/design-briefs/sw2102-landing-neon.html.
 *
 * This is the STATIC scaffold: structure + content only. The artifact's
 * boot-sequence overlay, ignition flash, starfield canvas animation,
 * cursor-reactive reticle, hold-to-charge warp buttons, and scroll-reveal
 * IntersectionObserver are all deferred to the WO-LANDING-NEON (b) follow-up
 * worker — this component renders the "already lit" end-state so the page
 * is fully usable (and testable) without any RAF/JS animation wired in.
 * See the `galaxyCanvasRef` marker below for the (b) hookup point.
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

// Static seed of the artifact's live-feed script (its first four `push()`
// calls) — the interval-driven scroll is (b)'s job; this renders that
// same starting content, inert.
const ARIA_FEED_SEED: FeedLine[] = [
  { tone: '', tag: 'ARIA>', text: 'online. plotting your first loop, Commander.' },
  { tone: 'sys', tag: 'SCAN>', text: 'sector 271 · 6 bodies · hazard band charted' },
  { tone: '', tag: 'ARIA>', text: 'route to Frontier Hub Lyra optimized (+1,204¢)' },
  { tone: 'warn', tag: 'ALERT>', text: 'hostile fleet in range — rerouting' },
];

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

export default function LandingPage({ onLogin, onRegister }: LandingPageProps) {
  const galaxyCanvasRef = useRef<HTMLCanvasElement>(null);
  const loopSectionRef = useRef<HTMLElement>(null);

  const scrollToLoop = () => {
    loopSectionRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="landing-root">
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
            <button className="landing-btn" onClick={onLogin}>
              Login
            </button>
            <button className="landing-btn landing-btn-primary" onClick={onRegister}>
              Join Now
            </button>
          </div>
        </div>
      </nav>

      <main>
      <header className="landing-hero">
        {/* WO-LANDING-NEON (b): canvas RAF + ignition wires here */}
        <canvas ref={galaxyCanvasRef} className="landing-galaxy-canvas" aria-hidden="true" />
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
              <button className="landing-warp" onClick={onRegister}>
                ⚡ HOLD TO JUMP IN
              </button>
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
            <div className="landing-mfd-body">
              {ARIA_FEED_SEED.map((line, i) => (
                <div key={i} className={`landing-feedline ${line.tone}`}>
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
            <button className="landing-warp" onClick={onRegister}>
              ⚡ HOLD TO ENLIST
            </button>
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
    </div>
  );
}
