"""LEG-4163/4165/4166/4167/4171/4172/4173 — table + wiring pins."""

from pathlib import Path

from src.models.faction import FactionType
from src.services.emergent_reputation_service import EMERGENT_ACTIONS

GS = Path(__file__).resolve().parents[2] / "src"


def _deltas(key: str):
    return [(d.faction, d.delta) for d in EMERGENT_ACTIONS[key].deltas]


def test_trade_luxury_fc_registered():
    assert _deltas("TRADE_LUXURY_FC") == [(FactionType.INDEPENDENTS, 2)]


def test_kill_wanted_player_fed_registered():
    assert _deltas("KILL_WANTED_PLAYER_FED") == [(FactionType.FEDERATION, 15)]


def test_quantum_scan_nova_registered():
    assert _deltas("QUANTUM_SCAN_NOVA") == [(FactionType.EXPLORERS, 1)]


def test_discover_sector_fed_registered():
    assert _deltas("DISCOVER_SECTOR_FED") == [(FactionType.FEDERATION, 10)]


def test_kill_bounty_target_fed_registered():
    assert _deltas("KILL_BOUNTY_TARGET_FED") == [(FactionType.FEDERATION, 25)]


def test_vote_frontier_gov_registered():
    assert _deltas("VOTE_FRONTIER_GOV") == [(FactionType.INDEPENDENTS, 2)]


def test_sell_exotic_tech_nova_registered():
    assert _deltas("SELL_EXOTIC_TECH_NOVA") == [(FactionType.EXPLORERS, 2)]


def test_defend_fed_sector_registered():
    assert _deltas("DEFEND_FED_SECTOR") == [(FactionType.FEDERATION, 20)]


def test_pay_region_tax_fed_registered():
    assert _deltas("PAY_REGION_TAX_FED") == [(FactionType.FEDERATION, 1)]


def test_trading_sell_wires_luxury_fc_and_exotic_nova():
    text = (GS / "api/routes/trading.py").read_text()
    assert 'station.faction_affiliation == "Frontier Coalition"' in text
    assert '("gourmet_food", "luxury_goods")' in text
    assert '"TRADE_LUXURY_FC"' in text
    assert "int(total_earnings // 10_000)" in text
    assert "apply_emergent_action(db, current_player, \"TRADE_LUXURY_FC\")" in text
    assert 'trade_request.resource_type == "exotic_technology"' in text
    assert 'station.faction_affiliation == "Nova Scientific Institute"' in text
    assert '"SELL_EXOTIC_TECH_NOVA"' in text
    assert "apply_trade_volume_rep(" in text
    assert "_award_pay_region_tax_fed" in text
    assert "region_tax_amount // 25000" in text
    assert '"PAY_REGION_TAX_FED"' in text
    # Tax cadence must not reuse the 5k trade-volume helper.
    helper = text.split("def _award_pay_region_tax_fed")[1].split("router = APIRouter")[0]
    assert "apply_trade_volume_rep(" not in helper
    assert "sector.zone_type == ZoneType.FEDERATION" in text


def test_combat_wires_wanted_and_bounty_fed():
    text = (GS / "services/combat_service.py").read_text()
    assert '"KILL_WANTED_PLAYER_FED"' in text
    assert "defender_is_live_wanted or defender_is_live_suspect" in text
    assert '"KILL_BOUNTY_TARGET_FED"' in text
    assert "ZoneType.FEDERATION" in text
    assert '"DEFEND_FED_SECTOR"' in text
    assert "drones_remaining > 0" in text
    assert "sector.zone.zone_type == ZoneType.FEDERATION" in text
    # LEG-4179: Wanted/Suspect award must share the bounty hook's Fed-zone gate.
    wanted = text.split("LEG-4165 + LEG-4179")[1].split("else:")[0]
    assert "kill_sector.zone.zone_type == ZoneType.FEDERATION" in wanted
    assert '"KILL_WANTED_PLAYER_FED"' in wanted
    assert "getattr(kill_sector, \"zone\", None) is not None" in wanted
    assert "Sector.zone_type" not in wanted


def test_fleet_wires_bounty_fed_on_paid_share():
    text = (GS / "services/fleet_service.py").read_text()
    assert '"KILL_BOUNTY_TARGET_FED"' in text
    assert 'share_result.get("paid", 0)' in text or "share_result.get('paid', 0)" in text
    assert "ZoneType.FEDERATION" in text


def test_quantum_scan_wires_nova_after_commit():
    text = (GS / "services/quantum_service.py").read_text()
    commit_at = text.find("db.commit()")
    hook_at = text.find('"QUANTUM_SCAN_NOVA"')
    assert commit_at != -1 and hook_at != -1 and hook_at > commit_at
    assert "quantum_scan_nova_rep_" in text
    assert "_NOVA_SCAN_DAILY_CAP = 5" in text


def test_movement_first_visit_wires_discover_sector_fed():
    text = (GS / "services/movement_service.py").read_text()
    assert '"DISCOVER_SECTOR_FED"' in text
    assert "NOVA_FIRST_SCAN_RESEARCH_SECTOR" in text
    assert "destination_sector.zone.zone_type == ZoneType.FEDERATION" in text
    # Must not use the wrong galaxy-first-discoverer-only key from the old branch.
    assert "DISCOVER_FED_SECTOR" not in text


def test_governance_policy_vote_wires_frontier_rep():
    text = (GS / "services/regional_governance_service.py").read_text()
    assert "_dispatch_vote_frontier_gov_rep" in text
    assert '"VOTE_FRONTIER_GOV"' in text
    assert "ZoneType.FRONTIER" in text
    # Policy path must call the hook; election path must not.
    policy = text.split("async def cast_policy_vote")[1].split("async def tally_election")[0]
    election = text.split("async def cast_election_vote")[1].split("async def register_candidate")[0]
    assert "_dispatch_vote_frontier_gov_rep" in policy
    assert "_dispatch_vote_frontier_gov_rep" not in election
