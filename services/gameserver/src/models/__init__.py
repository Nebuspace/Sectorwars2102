from src.models.user import User
from src.models.admin_credentials import AdminCredentials
from src.models.player_credentials import PlayerCredentials
from src.models.oauth_account import OAuthAccount
from src.models.refresh_token import RefreshToken
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification, ShipType, FailureType, UpgradeType, InsuranceType, ShipStatus
from src.models.reputation import Reputation, TeamReputation, ReputationLevel
from src.models.team import Team, TeamReputationHandling, TeamRecruitmentStatus
from src.models.team_member import TeamMember, TeamRole
from src.models.planet import Planet, player_planets
from src.models.station import Station, StationClass, StationType, StationStatus, player_stations

# New models
from src.models.galaxy import Galaxy
from src.models.region import (
    Region, RegionType, RegionStatus,
    RegionalMembership, RegionalPolicy, RegionalElection, RegionalVote, RegionalTreaty, InterRegionalTravel,
    RegionalPolicyVote, RegionalTreasuryEntry
)
from src.models.zone import Zone
from src.models.cluster import Cluster, ClusterType
from src.models.sector import Sector, SectorType, sector_warps
from src.models.warp_tunnel import WarpTunnel, WarpTunnelType, WarpTunnelStatus
from src.models.resource import Resource, ResourceType, ResourceQuality, Market
from src.models.combat_log import CombatLog, CombatStats
from src.models.game_event import GameEvent, EventTemplate, EventEffect, EventParticipation
from src.models.market_transaction import MarketTransaction as EnhancedMarketTransaction, MarketPrice, PriceHistory, EconomicMetrics, PriceAlert
from src.models.genesis_device import GenesisDevice, GenesisType, GenesisStatus, PlanetFormation
from src.models.special_formation import SpecialFormation, SpecialFormationType
from src.models.first_login import FirstLoginSession, DialogueExchange, PlayerFirstLoginState, ShipChoice, NegotiationSkillLevel, DialogueOutcome
from src.models.ai_trading import AIMarketPrediction, PlayerTradingProfile, AIRecommendation, AIModelPerformance, AITrainingData
from src.models.audit_log import AuditLog
from src.models.message import Message
from src.models.faction import Faction, FactionType, FactionMission
from src.models.drone import Drone, DroneType, DroneStatus, DroneDeployment, DroneCombat
from src.models.fleet import Fleet, FleetMember, FleetBattle, FleetBattleCasualty, FleetRole, FleetStatus, BattlePhase
from src.models.mfa import MFASecret, MFAAttempt
from src.models.translation import (
    Language, TranslationNamespace, TranslationKey, 
    UserLanguagePreference, TranslationAuditLog, TranslationProgress
)
from src.models.aria_personal_intelligence import (
    ARIAPersonalMemory, ARIAMarketIntelligence, ARIAExplorationMap,
    ARIATradingPattern, ARIAQuantumCache, ARIASecurityLog
)
from src.models.enhanced_ai_models import AIComprehensiveAssistant
from src.models.bang_generation_job import BangGenerationJob, BangGenerationJobStatus
from src.models.docking import DockingSlipOccupancy, DockingQueueEntry
from src.models.pending_engagement import PendingEngagement, EngagementStatus
from src.models.cargo_wreck import CargoWreck, WreckCause
from src.models.migration_contract import MigrationContract, MigrationContractStatus
from src.models.npc_character import (
    NPCCharacter,
    NPCArchetype,
    NPCStatus,
    NPCActivity,
    NPCLifecycleStage,
    NPCRoster,
    NPCDeathLog,
)
from src.models.construction import ConstructionReservation
from src.models.port_ownership import StationListing, PurchaseOffer, TakeoverCampaign
from src.models.warp_gate import WarpGate, WarpGateBeacon, WarpGateStatus, WarpGateBeaconStatus
from src.models.sector_celestial import SectorCelestial, SectorFeatureDiscovery
from src.models.processed_webhook_event import ProcessedWebhookEvent
from src.models.sector_faction_influence import SectorFactionInfluence
from src.models.medal import Medal, PlayerMedal
from src.models.bounty_claim import BountyClaim, BountyClaimStatus
from src.models.region_invite import RegionInvite, RegionInviteStatus, RegionInviteRedemption