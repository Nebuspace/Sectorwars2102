import React from 'react';
import { useSearchParams } from 'react-router-dom';
import { GameContext } from '../../contexts/GameContext';
import GameLayout from './GameLayout';

/**
 * LabShell — dev-only geometry harness for WO-UI0-PERSISTENT-SHELL lane B.
 *
 * Mounts the REAL <GameLayout> (same component + game-layout.css the live
 * /game routes use) under a MOCKED GameContext, so the container/sidebar/
 * deck/windshield-band geometry can be proven at 1440x900 for all three
 * grounded states WITHOUT a backend, auth, or Docker — mirrors /lab/vista's
 * "no Docker, no auth" harness pattern (playwright.vista-proof.config.ts).
 *
 * GameLayout is otherwise nested inside AuthProvider/WebSocketProvider/
 * GameProvider/AutopilotProvider at the App.tsx root (see App.tsx's Router
 * tree), so useAuth()/useWebSocket()/useAutopilot() resolve against the REAL
 * (unauthenticated-default, already-safe-under-null) providers exactly as
 * they do pre-hydration on a real /game route. Only GameContext needs an
 * explicit override here — it's the one piece whose real Provider can only
 * be populated by a live login flow, and it's the one lane A + this proof
 * both need to force (is_docked/is_landed) directly. GameContext.tsx exports
 * the raw context object (additive, zero-behavior-change: see its own
 * doc-comment) for exactly this purpose.
 *
 * Every consuming component under GameLayout (MFD pages, HUD, toasts) is
 * exercised with real, safely-defaulted data --- MFD pages additionally sit
 * behind MFDPageBoundary (see MFDScreen.tsx's own doc-comment), so even a
 * page that dereferences more than this stub supplies can only fault its
 * own viewport, never the outer geometry this harness exists to prove.
 */

type ShellMode = 'flight' | 'station' | 'surface';

const VALID_MODES: ShellMode[] = ['flight', 'station', 'surface'];

function parseMode(raw: string | null): ShellMode {
  return (VALID_MODES as string[]).includes(raw ?? '') ? (raw as ShellMode) : 'flight';
}

// Shared no-op stubs. `any[]` rest params make each one structurally
// assignable to every fixed-arity method GameContextType declares (a
// (...args: any[]) => X function type is assignable to any narrower
// signature returning an assignable X) — covers every action the mocked
// pages can invoke without hand-writing ~35 near-identical one-liners.
const noop = (..._args: any[]): void => {}; // eslint-disable-line @typescript-eslint/no-unused-vars
const asyncNoop = async (..._args: any[]): Promise<any> => undefined; // eslint-disable-line @typescript-eslint/no-unused-vars

const stubMigrationContract = {
  id: 'lab-contract',
  source_planet_id: 'lab-planet',
  source_sector_id: 1,
  cohort_total: 0,
  loaded: 0,
  delivered: 0,
  remaining_to_load: 0,
  fee_per_pioneer_locked: 0,
  status: 'BROKERED' as const,
};

/** Builds the full mocked GameContext value for a given ?mode=. */
function buildMockGameValue(mode: ShellMode): NonNullable<React.ContextType<typeof GameContext>> {
  return {
    playerState: {
      id: 'lab-shell-player',
      username: 'LAB-SHELL',
      credits: 125_000,
      turns: 480,
      max_turns: 500,
      current_sector_id: 1,
      is_docked: mode === 'station',
      is_landed: mode === 'surface',
      defense_drones: 0,
      attack_drones: 0,
      mines: 0,
      personal_reputation: 0,
      reputation_tier: 'Neutral',
      name_color: '#00D9FF',
      military_rank: 'Cadet',
    },
    refreshPlayerState: asyncNoop,
    updatePlayerCredits: noop,
    updateShipGenesis: noop,

    needsFirstLogin: false,
    checkFirstLoginStatus: async () => false,
    onFirstLoginComplete: asyncNoop,

    ships: [],
    currentShip: null,
    loadShips: asyncNoop,
    setCurrentShip: asyncNoop,

    currentSector: null,
    availableMoves: { warps: [], tunnels: [] },
    planetsInSector: [],
    stationsInSector: [],

    moveToSector: asyncNoop,
    getAvailableMoves: asyncNoop,
    scanForLatentTunnels: async () => undefined,

    dockAtStation: asyncNoop,
    undockFromStation: asyncNoop,
    getStationSlips: async () => null,
    bumpDockOccupant: asyncNoop,
    marketInfo: null,
    getMarketInfo: asyncNoop,
    buyResource: asyncNoop,
    sellResource: asyncNoop,

    claimPlanet: asyncNoop,
    landOnPlanet: asyncNoop,
    leavePlanet: asyncNoop,
    renamePlanet: asyncNoop,
    getPlanetDetails: asyncNoop,
    updatePlanetAllocation: asyncNoop,
    updatePlanetDefenses: asyncNoop,
    upgradePlanetBuilding: asyncNoop,
    transferColonists: asyncNoop,
    getPioneerOffice: async () => ({
      planet_id: 'lab-planet',
      planet_name: 'Lab Planet',
      fee_per_pioneer: 0,
      cargo_colonists: 0,
      cargo_free: 0,
      contracts: [],
    }),
    brokerMigrationContract: async () => stubMigrationContract,
    loadPioneerBatch: async () => stubMigrationContract,
    listMigrationContracts: async () => [],
    cancelMigrationContract: async () => stubMigrationContract,

    getCitadelInfo: asyncNoop,
    upgradeCitadel: asyncNoop,
    cancelCitadelUpgrade: asyncNoop,
    getDefenseBuildings: asyncNoop,
    buildDefenseBuilding: asyncNoop,
    depositToSafe: asyncNoop,
    withdrawFromSafe: asyncNoop,
    depositCommodityToSafe: asyncNoop,
    withdrawCommodityFromSafe: asyncNoop,
    setCitadelAutoDeposit: asyncNoop,
    deployMines: asyncNoop,
    getPlanetDefenseInfo: asyncNoop,
    upgradeShields: asyncNoop,

    getPortListings: asyncNoop,
    getListing: asyncNoop,
    listStation: asyncNoop,
    placeOffer: asyncNoop,
    getMyStations: asyncNoop,
    setStationTax: asyncNoop,
    withdrawTreasury: asyncNoop,
    getTakeoverStatus: asyncNoop,
    launchTakeover: asyncNoop,
    counterTakeover: asyncNoop,

    inboxMessages: [],
    unreadMessageCount: 0,
    refreshInbox: asyncNoop,
    sendPlayerMessage: async () => ({ message_id: 'lab-message', sent_at: new Date().toISOString() }),
    markMessageRead: asyncNoop,

    quantumStatus: null,
    refreshQuantumStatus: asyncNoop,
    quantumScan: async () => ({
      resonance: 'silent',
      texture: 'hollow',
      echo: 'silent',
      expires_at: '',
      scan_cooldown_until: null,
      turns_remaining: 0,
    }),
    quantumJump: async () => ({
      outcome: 'misfire',
      destination_sector_id: 0,
      destination_name: '',
      distance_jumped: 0,
      hull_damage_pct: 0,
      jump_cooldown_until: null,
      turns_remaining: 0,
    }),
    refineQuantumCharge: async () => ({ quantum_charges: 0, quantum_shards: 0 }),
    harvestNebula: async () => ({
      shard_yield: 0,
      crit: false,
      nebula_type: '',
      quantum_shards: 0,
      harvest_cooldown_until: null,
    }),
    quantumScanResult: null,
    setQuantumScanResult: noop,

    isLoading: false,
    isRefreshing: false,
    error: null,

    exploreCurrentLocation: asyncNoop,
  };
}

const LabShell: React.FC = () => {
  const [searchParams] = useSearchParams();
  const mode = parseMode(searchParams.get('mode'));
  const mockValue = buildMockGameValue(mode);

  return (
    <GameContext.Provider value={mockValue}>
      <GameLayout>
        {/* Stub deck child — GameLayout renders {children} full-bleed inside
            .main-viewport exactly as every real routed page does today (the
            "deck" slot is not yet nested apart from the windshield — lane A's
            job). Sized to fill so its rect is a meaningful full-bleed proof. */}
        <div
          data-testid="lab-shell-deck"
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
        >
          STUB DECK
        </div>
      </GameLayout>
      <div data-testid="lab-shell-ready" style={{ display: 'none' }} />
    </GameContext.Provider>
  );
};

export default LabShell;
