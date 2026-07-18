"""
Player Analytics Service
Handles computation and caching of player analytics data
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, text, and_, or_
import logging

from src.models.player import Player
from src.models.ship import Ship
from src.models.planet import Planet
from src.models.station import Station
from src.models.user import User
from src.models.player_analytics import PlayerSession, PlayerAnalyticsSnapshot, PlayerActivity

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Service for computing and caching player analytics"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_real_time_metrics(self) -> Dict[str, Any]:
        """
        Get current real-time analytics metrics from the database
        """
        try:
            # Get current timestamp for calculations
            now = datetime.utcnow()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            week_ago = now - timedelta(days=7)
            thirty_days_ago = now - timedelta(days=30)
            
            # Get all players with their related data
            all_players = self.db.query(Player).all()
            active_players = [p for p in all_players if p.is_active]
            
            # Calculate basic player metrics
            total_players = len(all_players)
            total_active_players = len(active_players)
            
            # Players online now -- last_login-within-1h approximation.
            # This is the Redis-down FALLBACK figure only: the route
            # (admin_comprehensive.get_real_time_analytics) overwrites this
            # with the live presence-set cardinality
            # (activity:online_players, player_activity_service
            # .get_online_player_count) whenever Redis is reachable, and
            # only falls back to this value when it isn't. Kept here,
            # unchanged, as that fallback.
            one_hour_ago = now - timedelta(hours=1)
            recent_users = self.db.query(User).filter(
                User.last_login >= one_hour_ago
            ).count()
            
            # New players today
            new_players_today = self.db.query(Player).filter(
                Player.created_at >= today_start
            ).count()
            
            # Calculate total credits in circulation
            total_credits = self.db.query(func.sum(Player.credits)).scalar() or 0
            average_credits = total_credits / total_players if total_players > 0 else 0
            
            # Count ships, planets, ports
            total_ships = self.db.query(Ship).count()
            total_planets = self.db.query(Planet).count()
            total_ports = self.db.query(Station).count()
            
            # Calculate session-based metrics
            avg_session_time = self._calculate_average_session_time(week_ago)
            
            # Calculate retention rates
            retention_7d = self._calculate_retention_rate(7)
            retention_30d = self._calculate_retention_rate(30)
            
            # Count suspicious activities (simplified)
            suspicious_activities = self.db.query(PlayerActivity).filter(
                PlayerActivity.flagged_for_review == True,
                PlayerActivity.timestamp >= today_start
            ).count()
            
            # Get detailed breakdowns
            ships_by_type = self._get_ships_by_type()
            players_by_status = self._get_players_by_status()
            resource_distribution = self._get_resource_distribution()
            
            return {
                # Core metrics
                "total_players": total_players,
                "total_active_players": total_active_players,
                "players_online_now": recent_users,
                "new_players_today": new_players_today,
                "new_players_week": self._get_new_players_week(),
                
                # Economic metrics
                "total_credits_circulation": int(total_credits),
                "average_credits_per_player": round(average_credits, 2),
                "total_ships": total_ships,
                "total_planets": total_planets,
                "total_ports": total_ports,
                "resource_distribution": resource_distribution,
                
                # Activity metrics
                "average_session_time": round(avg_session_time, 2),
                "total_actions_today": self._get_total_actions_today(),
                "player_retention_rate": round(retention_7d, 1),
                "player_retention_rate_7d": round(retention_7d, 1),
                "player_retention_rate_30d": round(retention_30d, 1),
                
                # Security metrics
                "suspicious_activity_alerts": suspicious_activities,
                "failed_login_attempts": 0,  # Would need to track login failures
                
                # Breakdowns
                "ships_by_type": ships_by_type,
                "players_by_status": players_by_status,
                "activity_by_hour": self._get_activity_by_hour(),
                
                # Metadata
                "last_updated": now.isoformat(),
                "calculation_time_ms": 0  # Could add timing if needed
            }
            
        except Exception as e:
            logger.error(f"Error calculating real-time metrics: {e}")
            return self._get_fallback_metrics()
    
    def _calculate_average_session_time(self, since: datetime) -> float:
        """Calculate average session time in hours"""
        try:
            # Get completed sessions since the given date
            completed_sessions = self.db.query(PlayerSession).filter(
                PlayerSession.start_time >= since,
                PlayerSession.end_time.isnot(None),
                PlayerSession.duration_minutes.isnot(None)
            ).all()
            
            if not completed_sessions:
                return 2.5  # Default fallback
            
            total_minutes = sum(session.duration_minutes for session in completed_sessions)
            avg_minutes = total_minutes / len(completed_sessions)
            return avg_minutes / 60.0  # Convert to hours
            
        except Exception as e:
            logger.error(f"Error calculating average session time: {e}")
            return 2.5
    
    def _calculate_retention_rate(self, days: int) -> float:
        """Calculate player retention rate over specified days"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Players who joined before the cutoff
            players_joined_before = self.db.query(Player).filter(
                Player.created_at <= cutoff_date
            ).count()
            
            if players_joined_before == 0:
                return 100.0
            
            # Of those players, how many are still active
            still_active = self.db.query(Player).filter(
                Player.created_at <= cutoff_date,
                Player.is_active == True
            ).count()
            
            return (still_active / players_joined_before) * 100.0
            
        except Exception as e:
            logger.error(f"Error calculating retention rate: {e}")
            return 85.0
    
    def _get_ships_by_type(self) -> Dict[str, int]:
        """Get count of ships by type"""
        try:
            result = self.db.query(
                Ship.type,
                func.count(Ship.id)
            ).group_by(Ship.type).all()
            
            return {ship_type.value: count for ship_type, count in result}
            
        except Exception as e:
            logger.error(f"Error getting ships by type: {e}")
            return {}
    
    def _get_players_by_status(self) -> Dict[str, int]:
        """Get count of players by status"""
        try:
            active_count = self.db.query(Player).filter(Player.is_active == True).count()
            inactive_count = self.db.query(Player).filter(Player.is_active == False).count()
            
            return {
                "active": active_count,
                "inactive": inactive_count
            }
            
        except Exception as e:
            logger.error(f"Error getting players by status: {e}")
            return {"active": 0, "inactive": 0}
    
    def _get_new_players_week(self) -> int:
        """Get count of new players this week"""
        try:
            week_ago = datetime.utcnow() - timedelta(days=7)
            return self.db.query(Player).filter(
                Player.created_at >= week_ago
            ).count()
            
        except Exception as e:
            logger.error(f"Error getting new players this week: {e}")
            return 0
    
    def _get_total_actions_today(self) -> int:
        """Get total player actions today"""
        try:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            return self.db.query(PlayerActivity).filter(
                PlayerActivity.timestamp >= today_start
            ).count()
            
        except Exception as e:
            logger.error(f"Error getting total actions today: {e}")
            return 0
    
    def _get_activity_by_hour(self) -> Dict[str, int]:
        """Get activity distribution by hour of day"""
        try:
            # Get activity for the last 24 hours
            twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
            
            # Query activities grouped by hour
            result = self.db.query(
                func.extract('hour', PlayerActivity.timestamp).label('hour'),
                func.count(PlayerActivity.id).label('count')
            ).filter(
                PlayerActivity.timestamp >= twenty_four_hours_ago
            ).group_by(
                func.extract('hour', PlayerActivity.timestamp)
            ).all()
            
            # Convert to dict with all 24 hours represented
            activity_by_hour = {str(i): 0 for i in range(24)}
            for hour, count in result:
                activity_by_hour[str(int(hour))] = count
            
            return activity_by_hour
            
        except Exception as e:
            logger.error(f"Error getting activity by hour: {e}")
            return {str(i): 0 for i in range(24)}
    
    def _get_fallback_metrics(self) -> Dict[str, Any]:
        """Return fallback metrics when calculation fails"""
        return {
            "total_players": 0,
            "total_active_players": 0,
            "players_online_now": 0,
            "new_players_today": 0,
            "new_players_week": 0,
            "total_credits_circulation": 0,
            "average_credits_per_player": 0.0,
            "total_ships": 0,
            "total_planets": 0,
            "total_ports": 0,
            "average_session_time": 0.0,
            "total_actions_today": 0,
            "player_retention_rate": 0.0,
            "player_retention_rate_7d": 0.0,
            "player_retention_rate_30d": 0.0,
            "suspicious_activity_alerts": 0,
            "failed_login_attempts": 0,
            "ships_by_type": {},
            "players_by_status": {"active": 0, "inactive": 0},
            "activity_by_hour": {str(i): 0 for i in range(24)},
            "last_updated": datetime.utcnow().isoformat(),
            "calculation_time_ms": 0
        }
    
    def create_analytics_snapshot(self, snapshot_type: str = "hourly") -> PlayerAnalyticsSnapshot:
        """Create and save an analytics snapshot"""
        try:
            metrics = self.get_real_time_metrics()
            
            snapshot = PlayerAnalyticsSnapshot(
                snapshot_type=snapshot_type,
                total_players=metrics["total_players"],
                active_players=metrics["total_active_players"],
                online_players=metrics["players_online_now"],
                new_players_today=metrics["new_players_today"],
                new_players_week=metrics["new_players_week"],
                total_credits_circulation=metrics["total_credits_circulation"],
                average_credits_per_player=metrics["average_credits_per_player"],
                total_ships=metrics["total_ships"],
                total_planets=metrics["total_planets"],
                total_ports=metrics["total_ports"],
                average_session_time=metrics["average_session_time"],
                total_actions_today=metrics["total_actions_today"],
                player_retention_rate_7d=metrics["player_retention_rate_7d"],
                player_retention_rate_30d=metrics["player_retention_rate_30d"],
                suspicious_activity_alerts=metrics["suspicious_activity_alerts"],
                failed_login_attempts=metrics["failed_login_attempts"],
                player_by_status=metrics["players_by_status"],
                ships_by_type=metrics["ships_by_type"],
                activity_by_hour=metrics["activity_by_hour"]
            )
            
            self.db.add(snapshot)
            self.db.commit()
            self.db.refresh(snapshot)
            
            logger.info(f"Created analytics snapshot: {snapshot_type}")
            return snapshot
            
        except Exception as e:
            logger.error(f"Error creating analytics snapshot: {e}")
            self.db.rollback()
            raise
    
    def _get_resource_distribution(self) -> Dict[str, float]:
        """
        Calculate resource distribution across ports
        """
        try:
            from src.models.station import Station
            # Get all ports and their resource types
            total_ports = self.db.query(Station).count()
            if total_ports == 0:
                return {'Food': 25.0, 'Tech': 25.0, 'Ore': 25.0, 'Fuel': 25.0}
            
            # Query resource distribution from ports
            # This is a simplified version - would need actual resource data structure
            resource_counts = {
                'Food': self.db.query(Station).filter(Station.station_class.like('%food%')).count(),
                'Tech': self.db.query(Station).filter(Station.station_class.like('%tech%')).count(), 
                'Ore': self.db.query(Station).filter(Station.station_class.like('%ore%')).count(),
                'Fuel': self.db.query(Station).filter(Station.station_class.like('%fuel%')).count()
            }
            
            # Calculate percentages
            distribution = {}
            for resource, count in resource_counts.items():
                distribution[resource] = round((count / total_ports) * 100, 1) if total_ports > 0 else 25.0
            
            return distribution
            
        except Exception as e:
            logger.error(f"Error calculating resource distribution: {e}")
            return {'Food': 25.0, 'Tech': 25.0, 'Ore': 25.0, 'Fuel': 25.0}