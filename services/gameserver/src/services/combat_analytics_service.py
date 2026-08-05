"""
Combat Analytics Service for Admin Dashboard
"""

import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, desc

from src.models.combat_log import CombatLog
from src.models.player import Player
from src.models.ship import Ship
from src.models.planet import Planet
from src.models.fleet import FleetBattle
from src.models.sector import Sector
from src.services.audit_service import AuditService, AuditAction


class CombatAnalyticsService:
    def __init__(self, db: Session):
        self.db = db
        self.audit_service = AuditService(db)
    
    def get_live_combat_feed(self, 
                            limit: int = 50,
                            combat_type: Optional[str] = None,
                            sector_id: Optional[uuid.UUID] = None,
                            active_only: bool = True) -> List[Dict[str, Any]]:
        """Get live/recent combat activities"""
        query = self.db.query(CombatLog)
        
        if active_only:
            # For active combats, look for those that haven't ended yet
            query = query.filter(CombatLog.ended_at.is_(None))
        else:
            # Include recent completed combats
            query = query.filter(
                or_(
                    CombatLog.ended_at.is_(None),
                    CombatLog.started_at >= datetime.utcnow() - timedelta(minutes=30)
                )
            )
        
        if combat_type:
            query = query.filter(CombatLog.combat_type == combat_type)
        
        if sector_id:
            query = query.filter(CombatLog.sector_uuid == sector_id)
        
        # Order by most recent first
        combats = query.order_by(desc(CombatLog.started_at)).limit(limit).all()
        
        # Format combat data
        combat_feed = []
        for combat in combats:
            # Get participant details (assuming both are players for now)
            attacker = self._get_participant_info(combat.attacker_id, "player")
            defender = self._get_participant_info(combat.defender_id, "player")
            
            # Get sector info
            sector = self.db.query(Sector).filter(Sector.id == combat.sector_uuid).first()
            
            # Determine status based on ended_at and outcome
            if combat.ended_at is None:
                status = "in_progress"
            else:
                status = "completed"
            
            # Determine victor from outcome
            victor_id = None
            if combat.outcome == "attacker_win":
                victor_id = combat.attacker_id
            elif combat.outcome == "defender_win":
                victor_id = combat.defender_id
            
            combat_data = {
                "id": str(combat.id),
                "combat_type": combat.combat_type,
                "status": status,
                "started_at": combat.started_at.isoformat(),
                "ended_at": combat.ended_at.isoformat() if combat.ended_at else None,
                "duration_seconds": (
                    (combat.ended_at - combat.started_at).total_seconds() 
                    if combat.ended_at else 
                    (datetime.utcnow() - combat.started_at).total_seconds()
                ),
                "current_round": combat.rounds,
                "sector": {
                    "id": str(sector.id) if sector else None,
                    "coordinates": f"[{sector.x_coord},{sector.y_coord},{sector.z_coord}]" if sector else "Unknown",
                    "name": sector.name if sector and sector.name else "Unknown Sector"
                },
                "attacker": attacker,
                "defender": defender,
                "combat_stats": {
                    "attacker_damage_dealt": combat.attacker_damage_dealt,
                    "defender_damage_dealt": combat.defender_damage_dealt,
                    "attacker_drones_lost": combat.attacker_drones_lost,
                    "defender_drones_lost": combat.defender_drones_lost,
                    "attacker_drones": combat.attacker_drones,
                    "defender_drones": combat.defender_drones
                },
                "victor_id": str(victor_id) if victor_id else None,
                "is_active": status == "in_progress",
                "needs_intervention": self._check_intervention_needed(combat)
            }
            
            combat_feed.append(combat_data)
        
        # Also check for fleet battles
        fleet_battles = (
            self.db.query(FleetBattle)
            .filter(FleetBattle.ended_at.is_(None))  # Active battles have no end time
            .order_by(desc(FleetBattle.started_at))
            .limit(10)
            .all()
        )
        
        for battle in fleet_battles:
            # Determine status from phase and ended_at
            if battle.ended_at is None:
                status = "in_progress"
            else:
                status = "completed"
                
            # Determine victor
            victor_id = None
            if battle.winner == "attacker":
                victor_id = battle.attacker_fleet_id
            elif battle.winner == "defender":
                victor_id = battle.defender_fleet_id
                
            combat_feed.append({
                "id": str(battle.id),
                "combat_type": "fleet_battle",
                "status": status,
                "started_at": battle.started_at.isoformat(),
                "ended_at": battle.ended_at.isoformat() if battle.ended_at else None,
                "duration_seconds": (
                    (battle.ended_at - battle.started_at).total_seconds() 
                    if battle.ended_at else 
                    (datetime.utcnow() - battle.started_at).total_seconds()
                ),
                "current_round": 0,  # Fleet battles don't have rounds, just phases
                "sector": {"id": str(battle.sector_id) if battle.sector_id else None, "coordinates": "Fleet Space", "name": "Fleet Battle Zone"},
                "attacker": {"id": str(battle.attacker_fleet_id) if battle.attacker_fleet_id else None, "type": "fleet", "name": "Attacking Fleet"},
                "defender": {"id": str(battle.defender_fleet_id) if battle.defender_fleet_id else None, "type": "fleet", "name": "Defending Fleet"},
                "combat_stats": {
                    "attacker_ships_destroyed": battle.attacker_ships_destroyed,
                    "defender_ships_destroyed": battle.defender_ships_destroyed,
                    "attacker_ships_retreated": battle.attacker_ships_retreated,
                    "defender_ships_retreated": battle.defender_ships_retreated,
                    "total_damage": battle.total_damage_dealt
                },
                "victor_id": str(victor_id) if victor_id else None,
                "is_active": status == "in_progress",
                "needs_intervention": False
            })
        
        # Sort by activity and timestamp
        combat_feed.sort(key=lambda x: (x['is_active'], x['started_at']), reverse=True)
        
        return combat_feed[:limit]
    
    def intervene_in_combat(self, combat_id: uuid.UUID, 
                           intervention_type: str,
                           parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Perform admin intervention in ongoing combat"""
        combat = self.db.query(CombatLog).filter(CombatLog.id == combat_id).first()
        
        if not combat:
            raise ValueError(f"Combat {combat_id} not found")
        
        # Check if combat is still active (no ended_at)
        if combat.ended_at is not None:
            raise ValueError("Can only intervene in active combat")
        
        intervention_id = uuid.uuid4()
        admin_id = parameters.get('admin_id')
        
        try:
            if intervention_type == "stop_combat":
                result = self._stop_combat(combat, parameters)
            elif intervention_type == "adjust_damage":
                result = self._adjust_combat_damage(combat, parameters)
            elif intervention_type == "restore_shields":
                result = self._restore_shields(combat, parameters)
            elif intervention_type == "declare_winner":
                result = self._declare_winner(combat, parameters)
            else:
                raise ValueError(f"Unknown intervention type: {intervention_type}")
            
            # Log the intervention
            self.audit_service.log_action(
                user_id=admin_id,
                action=AuditAction.INTERVENTION,
                resource_type="combat",
                resource_id=str(combat_id),
                details={
                    "intervention_id": str(intervention_id),
                    "intervention_type": intervention_type,
                    "parameters": {k: str(v) for k, v in parameters.items()},
                    "result": result
                }
            )
            
            self.db.commit()
            
            return {
                "intervention_id": str(intervention_id),
                "combat_id": str(combat_id),
                "type": intervention_type,
                "status": "success",
                "timestamp": datetime.utcnow().isoformat(),
                "result": result,
                "message": f"Combat intervention '{intervention_type}' completed successfully"
            }
            
        except Exception as e:
            self.db.rollback()
            
            # Log failed intervention
            self.audit_service.log_action(
                user_id=admin_id,
                action=AuditAction.INTERVENTION,
                resource_type="combat",
                resource_id=str(combat_id),
                details={
                    "intervention_id": str(intervention_id),
                    "status": "failed",
                    "error": str(e)
                }
            )
            
            raise
    
    def get_combat_balance_analytics(self, 
                                   timeframe: str = "7d",
                                   group_by: str = "ship_type") -> Dict[str, Any]:
        """Analyze combat balance and win rates"""
        # Parse timeframe
        hours = self._parse_timeframe(timeframe)
        start_time = datetime.utcnow() - timedelta(hours=hours)
        
        # Get completed combats (those with ended_at)
        combats = (
            self.db.query(CombatLog)
            .filter(
                CombatLog.ended_at.isnot(None),
                CombatLog.ended_at >= start_time
            )
            .all()
        )
        
        # Analyze by grouping
        if group_by == "ship_type":
            analytics = self._analyze_by_ship_type(combats)
        elif group_by == "player_level":
            analytics = self._analyze_by_player_level(combats)
        elif group_by == "combat_type":
            analytics = self._analyze_by_combat_type(combats)
        else:
            analytics = self._analyze_overall(combats)
        
        # Calculate balance metrics
        balance_metrics = self._calculate_balance_metrics(analytics)
        
        # Get outliers (overpowered/underpowered)
        outliers = self._identify_balance_outliers(analytics)
        
        return {
            "timeframe": timeframe,
            "total_combats": len(combats),
            "group_by": group_by,
            "analytics": analytics,
            "balance_metrics": balance_metrics,
            "outliers": outliers,
            "recommendations": self._generate_balance_recommendations(outliers)
        }
    
    def get_combat_disputes(self, 
                          status: Optional[str] = None,
                          limit: int = 50) -> List[Dict[str, Any]]:
        """Get combat-related disputes and issues"""
        disputes = []
        
        # Check for suspicious combat patterns
        suspicious_combats = self._find_suspicious_combats()
        
        # Check for reported issues (would need a dispute table)
        # For now, we'll identify potential disputes based on patterns
        
        # High damage disparities
        disparity_issues = (
            self.db.query(CombatLog)
            .filter(
                CombatLog.ended_at.isnot(None),
                CombatLog.ended_at >= datetime.utcnow() - timedelta(days=1)
            )
            .all()
        )
        
        for combat in disparity_issues:
            # Check for extreme damage disparities that might indicate bugs
            if combat.attacker_damage_dealt > 0 and combat.defender_damage_dealt > 0:
                ratio = max(
                    combat.attacker_damage_dealt / combat.defender_damage_dealt,
                    combat.defender_damage_dealt / combat.attacker_damage_dealt
                )
                
                if ratio > 10:  # 10:1 damage ratio is suspicious
                    disputes.append({
                        "id": str(uuid.uuid4()),
                        "combat_id": str(combat.id),
                        "type": "damage_disparity",
                        "severity": "high" if ratio > 20 else "medium",
                        "timestamp": combat.ended_at.isoformat(),
                        "description": f"Extreme damage ratio detected: {ratio:.1f}:1",
                        "participants": {
                            "attacker": str(combat.attacker_id),
                            "defender": str(combat.defender_id)
                        },
                        "status": "pending_review",
                        "recommended_action": "Investigate combat logs for potential exploits"
                    })
        
        # Add suspicious combat patterns
        for pattern in suspicious_combats:
            disputes.append({
                "id": str(uuid.uuid4()),
                "combat_id": None,
                "type": pattern['type'],
                "severity": pattern['severity'],
                "timestamp": datetime.utcnow().isoformat(),
                "description": pattern['description'],
                "participants": pattern.get('participants', {}),
                "status": "pending_review",
                "recommended_action": pattern['action']
            })
        
        # Filter by status if requested
        if status:
            disputes = [d for d in disputes if d['status'] == status]
        
        # Sort by severity and timestamp
        disputes.sort(key=lambda x: (
            {'high': 0, 'medium': 1, 'low': 2}.get(x['severity'], 3),
            x['timestamp']
        ))
        
        return disputes[:limit]
    
    # Helper methods
    
    def _get_participant_info(self, participant_id: uuid.UUID, 
                            participant_type: str) -> Dict[str, Any]:
        """Get participant information for combat feed"""
        info = {
            "id": str(participant_id),
            "type": participant_type,
            "name": "Unknown"
        }
        
        if participant_type == "player":
            player = self.db.query(Player).filter(Player.id == participant_id).first()
            if player:
                info["name"] = player.nickname or "Unknown"
                info["team_id"] = str(player.team_id) if player.team_id else None
        elif participant_type == "ship":
            ship = self.db.query(Ship).filter(Ship.id == participant_id).first()
            if ship:
                info["name"] = f"{ship.ship_type} ({ship.name})"
                info["owner_id"] = str(ship.owner_id) if ship.owner_id else None
        elif participant_type == "planet":
            planet = self.db.query(Planet).filter(Planet.id == participant_id).first()
            if planet:
                info["name"] = planet.name
                info["owner_id"] = str(planet.owner_id) if planet.owner_id else None
        
        return info
    
    def _check_intervention_needed(self, combat: CombatLog) -> bool:
        """Check if combat needs admin intervention"""
        # Long-running combat
        if combat.rounds > 100:
            return True
        
        # Stalemate detection
        if combat.rounds > 20:
            if (combat.attacker_damage_dealt < 100 and 
                combat.defender_damage_dealt < 100):
                return True
        
        # One-sided combat lasting too long
        if combat.rounds > 50:
            total_damage = combat.attacker_damage_dealt + combat.defender_damage_dealt
            if total_damage > 0:
                attacker_ratio = combat.attacker_damage_dealt / total_damage
                if attacker_ratio > 0.95 or attacker_ratio < 0.05:
                    return True
        
        return False
    
    def _parse_timeframe(self, timeframe: str) -> int:
        """Parse timeframe string to hours"""
        if timeframe.endswith('h'):
            return int(timeframe[:-1])
        elif timeframe.endswith('d'):
            return int(timeframe[:-1]) * 24
        elif timeframe.endswith('w'):
            return int(timeframe[:-1]) * 24 * 7
        else:
            return 24  # Default to 24 hours
    
    def _stop_combat(self, combat: CombatLog, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Stop an ongoing combat"""
        combat.outcome = "draw"  # Set to draw as admin stopped it
        combat.ended_at = datetime.utcnow()
        
        # Return remaining resources to participants
        # This would need to restore ships/resources as appropriate
        
        return {
            "action": "combat_stopped",
            "combat_id": str(combat.id),
            "reason": parameters.get('reason', 'Admin intervention')
        }
    
    def _adjust_combat_damage(self, combat: CombatLog, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Adjust damage values in combat"""
        target = parameters.get('target')  # 'attacker' or 'defender'
        damage_multiplier = parameters.get('damage_multiplier', 1.0)
        
        if target == 'attacker':
            combat.attacker_damage_dealt = int(combat.attacker_damage_dealt * damage_multiplier)
        elif target == 'defender':
            combat.defender_damage_dealt = int(combat.defender_damage_dealt * damage_multiplier)
        
        return {
            "action": "damage_adjusted",
            "target": target,
            "multiplier": damage_multiplier
        }
    
    def _restore_shields(self, combat: CombatLog, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Restore shields for combat participants"""
        target = parameters.get('target', 'both')  # 'attacker', 'defender', or 'both'
        shield_percent = parameters.get('shield_percent', 50)
        
        # Note: The CombatLog model doesn't track shields directly
        # This would need to update the actual Ship models
        # For now, we can log the action
        
        return {
            "action": "shields_restored",
            "target": target,
            "shield_percent": shield_percent,
            "note": "Shield restoration would be applied to ship models"
        }
    
    def _declare_winner(self, combat: CombatLog, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Manually declare a combat winner"""
        winner = parameters.get('winner')  # 'attacker' or 'defender'
        
        combat.ended_at = datetime.utcnow()
        
        if winner == 'attacker':
            combat.outcome = "attacker_win"
        elif winner == 'defender':
            combat.outcome = "defender_win"
        
        return {
            "action": "winner_declared",
            "winner": winner,
            "combat_id": str(combat.id)
        }
    
    def _analyze_by_ship_type(self, combats: List[CombatLog]) -> Dict[str, Any]:
        """Analyze combat performance by ship type"""
        ship_stats = {}

        for combat in combats:
            # Use stored ship type from combat log directly
            ship_type = combat.attacker_ship_type
            if not ship_type:
                continue

            if ship_type not in ship_stats:
                ship_stats[ship_type] = {
                    'wins': 0, 'losses': 0, 'total': 0,
                    'damage_dealt': 0, 'damage_taken': 0
                }

            ship_stats[ship_type]['total'] += 1
            ship_stats[ship_type]['damage_dealt'] += combat.attacker_damage_dealt
            ship_stats[ship_type]['damage_taken'] += combat.defender_damage_dealt

            if combat.outcome == "attacker_win":
                ship_stats[ship_type]['wins'] += 1
            else:
                ship_stats[ship_type]['losses'] += 1

        # Calculate win rates
        for ship_type, stats in ship_stats.items():
            if stats['total'] > 0:
                stats['win_rate'] = stats['wins'] / stats['total']
                stats['avg_damage_dealt'] = stats['damage_dealt'] / stats['total']
                stats['avg_damage_taken'] = stats['damage_taken'] / stats['total']

        return ship_stats
    
    def _analyze_by_player_level(self, combats: List[CombatLog]) -> Dict[str, Any]:
        """Analyze combat performance by player rank"""
        rank_stats = {
            'all_players': {
                'wins': 0, 'losses': 0, 'total': 0
            }
        }

        for combat in combats:
            if combat.attacker_id:
                player = self.db.query(Player).filter(Player.id == combat.attacker_id).first()
                if player:
                    rank = player.military_rank or "Recruit"
                    if rank not in rank_stats:
                        rank_stats[rank] = {'wins': 0, 'losses': 0, 'total': 0}

                    rank_stats[rank]['total'] += 1
                    rank_stats['all_players']['total'] += 1
                    if combat.outcome == "attacker_win":
                        rank_stats[rank]['wins'] += 1
                        rank_stats['all_players']['wins'] += 1
                    else:
                        rank_stats[rank]['losses'] += 1
                        rank_stats['all_players']['losses'] += 1

        # Calculate win rates
        for rank, stats in rank_stats.items():
            if stats['total'] > 0:
                stats['win_rate'] = stats['wins'] / stats['total']

        return rank_stats
    
    def _analyze_by_combat_type(self, combats: List[CombatLog]) -> Dict[str, Any]:
        """Analyze combat statistics by type"""
        type_stats = {}
        
        for combat in combats:
            ctype = combat.combat_type
            if ctype not in type_stats:
                type_stats[ctype] = {
                    'count': 0,
                    'avg_duration': 0,
                    'avg_rounds': 0,
                    'total_damage': 0
                }
            
            type_stats[ctype]['count'] += 1
            
            if combat.ended_at:
                duration = (combat.ended_at - combat.started_at).total_seconds()
                type_stats[ctype]['avg_duration'] += duration
            
            type_stats[ctype]['avg_rounds'] += combat.rounds
            type_stats[ctype]['total_damage'] += (
                combat.attacker_damage_dealt + combat.defender_damage_dealt
            )
        
        # Calculate averages
        for ctype, stats in type_stats.items():
            if stats['count'] > 0:
                stats['avg_duration'] /= stats['count']
                stats['avg_rounds'] /= stats['count']
                stats['avg_damage'] = stats['total_damage'] / stats['count']
        
        return type_stats
    
    def _analyze_overall(self, combats: List[CombatLog]) -> Dict[str, Any]:
        """Overall combat statistics"""
        total = len(combats)
        
        return {
            'total_combats': total,
            'by_outcome': {
                'attacker_win': len([c for c in combats if c.outcome == 'attacker_win']),
                'defender_win': len([c for c in combats if c.outcome == 'defender_win']),
                'draw': len([c for c in combats if c.outcome == 'draw']),
                'escaped': len([c for c in combats if c.outcome == 'escaped'])
            },
            'avg_duration': sum(
                (c.ended_at - c.started_at).total_seconds() 
                for c in combats if c.ended_at
            ) / total if total > 0 else 0,
            'avg_rounds': sum(c.rounds for c in combats) / total if total > 0 else 0
        }
    
    def _calculate_balance_metrics(self, analytics: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate overall balance metrics"""
        win_rates = []
        
        # Extract win rates from analytics
        for key, stats in analytics.items():
            if isinstance(stats, dict) and 'win_rate' in stats:
                win_rates.append(stats['win_rate'])
        
        if not win_rates:
            return {"balance_score": 100, "variance": 0}
        
        # Calculate variance from ideal 50% win rate
        ideal_rate = 0.5
        variance = sum((rate - ideal_rate) ** 2 for rate in win_rates) / len(win_rates)
        
        # Balance score (100 = perfect balance, 0 = completely unbalanced)
        balance_score = max(0, 100 - (variance * 1000))
        
        return {
            "balance_score": round(balance_score, 1),
            "variance": round(variance, 4),
            "min_win_rate": round(min(win_rates), 3),
            "max_win_rate": round(max(win_rates), 3),
            "spread": round(max(win_rates) - min(win_rates), 3)
        }
    
    def _identify_balance_outliers(self, analytics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Identify overpowered or underpowered entities"""
        outliers = []
        
        for entity, stats in analytics.items():
            if isinstance(stats, dict) and 'win_rate' in stats and stats.get('total', 0) >= 10:
                win_rate = stats['win_rate']
                
                # Overpowered (>70% win rate)
                if win_rate > 0.7:
                    outliers.append({
                        "entity": entity,
                        "type": "overpowered",
                        "win_rate": round(win_rate, 3),
                        "sample_size": stats['total'],
                        "severity": "high" if win_rate > 0.8 else "medium"
                    })
                
                # Underpowered (<30% win rate)
                elif win_rate < 0.3:
                    outliers.append({
                        "entity": entity,
                        "type": "underpowered",
                        "win_rate": round(win_rate, 3),
                        "sample_size": stats['total'],
                        "severity": "high" if win_rate < 0.2 else "medium"
                    })
        
        return outliers
    
    def _generate_balance_recommendations(self, outliers: List[Dict[str, Any]]) -> List[str]:
        """Generate balance recommendations based on outliers"""
        recommendations = []
        
        for outlier in outliers:
            if outlier['type'] == 'overpowered':
                recommendations.append(
                    f"Consider nerfing {outlier['entity']}: "
                    f"{outlier['win_rate']*100:.1f}% win rate is too high"
                )
            else:
                recommendations.append(
                    f"Consider buffing {outlier['entity']}: "
                    f"{outlier['win_rate']*100:.1f}% win rate is too low"
                )
        
        if not recommendations:
            recommendations.append("Combat balance appears healthy, no immediate changes needed")
        
        return recommendations
    
    def _find_suspicious_combats(self) -> List[Dict[str, Any]]:
        """Find potentially suspicious combat patterns"""
        suspicious = []
        
        # Check for rapid repeated combats between same players
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        
        repeat_combats = (
            self.db.query(
                CombatLog.attacker_id,
                CombatLog.defender_id,
                func.count(CombatLog.id).label('combat_count')
            )
            .filter(
                CombatLog.started_at >= one_hour_ago,
                CombatLog.attacker_id.isnot(None),
                CombatLog.defender_id.isnot(None)
            )
            .group_by(CombatLog.attacker_id, CombatLog.defender_id)
            .having(func.count(CombatLog.id) > 5)
            .all()
        )
        
        for result in repeat_combats:
            attacker = self.db.query(Player).filter(Player.id == result.attacker_id).first()
            defender = self.db.query(Player).filter(Player.id == result.defender_id).first()
            
            suspicious.append({
                "type": "repeat_combat",
                "severity": "high" if result.combat_count > 10 else "medium",
                "description": f"{result.combat_count} combats between same players in 1 hour",
                "participants": {
                    "attacker": {
                        "id": str(result.attacker_id),
                        "name": attacker.nickname if attacker else "Unknown"
                    },
                    "defender": {
                        "id": str(result.defender_id),
                        "name": defender.nickname if defender else "Unknown"
                    }
                },
                "action": "Investigate for potential combat farming or harassment"
            })
        
        return suspicious