/**
 * Vista Engine — Deterministic PRNG  (SeedBus)
 *
 * Algorithm: SplitMix32 — 32-bit variant of SplitMix (Steele, Lea & Flood 2014).
 *
 * WHY SplitMix32 (not 64):
 *   The client already uses this exact algorithm in SolarSystemViewscreen.tsx
 *   (`splitmix32`, L268).  Arithmetic stays inside safe 32-bit integers with
 *   `Math.imul` + `>>> 0`, so there is NO BigInt overhead and NO floating-point
 *   precision loss above 2^53.  All state is a single `number >>> 0` — fully
 *   reproducible across every modern JS engine.
 *
 *   The Python server uses SplitMix64; cross-language byte-parity is explicitly
 *   out of scope (BRIEF §6.4 — "downstream-deterministic from server inputs
 *   is sufficient").  The server owns all game numbers; the vista is a cosmetic
 *   layer seeded from already-committed data.
 *
 * Determinism guarantee:
 *   SeedBus(seed).streamName produces the SAME draw sequence for the SAME seed
 *   string in every JS runtime that honours IEEE-754 double-precision and spec
 *   Math.imul.  The only non-determinism source is excluded: no Math.random(),
 *   no Date.now(), no external entropy.
 *
 * Stream isolation:
 *   Each named child stream is seeded by fnv1a32(seed + ':' + streamName).
 *   Stream names never collide (they are distinct strings), so adding or
 *   reordering pipeline stages never shifts another stream's draws.
 *
 * No new npm deps — pure TS, no test framework.
 */

// ---------------------------------------------------------------------------
// FNV-1a 32-bit string hash  (stable, fast, well-distributed)
// ---------------------------------------------------------------------------

/** Hash a string to a non-negative 32-bit integer (unsigned). */
function fnv1a32(str: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    hash = hash ^ str.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
    hash = hash >>> 0;
  }
  return hash >>> 0;
}

/** Derive an integer seed for a named child stream from a string parent seed. */
export function deriveChildSeed(parentSeed: string, childName: string): number {
  return fnv1a32(`${parentSeed}:${childName}`);
}

// ---------------------------------------------------------------------------
// SeededRng — SplitMix32 instance
// ---------------------------------------------------------------------------

/**
 * A single, stateful SplitMix32 PRNG stream.
 * All helpers are deterministic functions of the stream state — no global state.
 */
export class SeededRng {
  private s: number;

  constructor(seed: number) {
    this.s = seed >>> 0;
  }

  /** Advance state and return a raw 32-bit unsigned integer. */
  private nextU32(): number {
    this.s = (this.s + 0x9e3779b9) >>> 0;
    let t = this.s ^ (this.s >>> 16);
    t = Math.imul(t, 0x21f0aaad);
    t = t ^ (t >>> 15);
    t = Math.imul(t, 0x735a2d97);
    return (t ^ (t >>> 15)) >>> 0;
  }

  /** Uniform float in [0, 1). */
  next01(): number {
    return this.nextU32() / 4294967296;
  }

  /** Uniform integer in [min, max] inclusive. */
  int(min: number, max: number): number {
    if (max <= min) return min;
    return min + (this.nextU32() % (max - min + 1));
  }

  /** Pick a uniformly random element from a non-empty array. */
  pick<T>(arr: readonly T[]): T {
    return arr[this.nextU32() % arr.length];
  }

  /**
   * Pick an element from `items` weighted by the parallel `weights` array
   * (relative, need not sum to 1).  Falls back to the last item on empty input.
   */
  pickWeighted<T>(items: readonly T[], weights: readonly number[]): T {
    if (items.length === 0) {
      throw new Error('pickWeighted: items must be non-empty');
    }
    let total = 0;
    for (let i = 0; i < weights.length; i++) total += weights[i];
    let roll = this.next01() * total;
    for (let i = 0; i < items.length; i++) {
      roll -= weights[i];
      if (roll < 0) return items[i];
    }
    return items[items.length - 1];
  }
}

// ---------------------------------------------------------------------------
// SeedBus — named child stream factory
// ---------------------------------------------------------------------------

/**
 * The 10 named pipeline sub-streams.
 * Each corresponds to one stage of generateVista() (BRIEF §2.7).
 * Adding / removing a stage never shifts another stream's draws.
 */
export type StreamName =
  | 'archetype'
  | 'palette'
  | 'sky'
  | 'celestial'
  | 'atmo'
  | 'terrain'
  | 'water'
  | 'features'
  | 'hazard'
  | 'grid';

const STREAM_NAMES: readonly StreamName[] = [
  'archetype',
  'palette',
  'sky',
  'celestial',
  'atmo',
  'terrain',
  'water',
  'features',
  'hazard',
  'grid',
];

export type SeedBusResult = Record<StreamName, SeededRng>;

/**
 * Split a string seed into 10 independent, named PRNG sub-streams.
 *
 * Each stream is a fresh SeededRng seeded by fnv1a32(seed + ':' + streamName).
 * Streams are independent — draws from 'archetype' never affect 'terrain', etc.
 *
 * Usage:
 *   const bus = SeedBus(input.seed);
 *   const archetype = bus.archetype.pick(profile.archetypes);
 *   const hue = bus.palette.int(0, 359);
 */
export function SeedBus(seed: string): SeedBusResult {
  const result = {} as SeedBusResult;
  for (const name of STREAM_NAMES) {
    result[name] = new SeededRng(deriveChildSeed(seed, name));
  }
  return result;
}
