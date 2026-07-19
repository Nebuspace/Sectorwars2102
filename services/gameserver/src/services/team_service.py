"""
Team management service for handling team operations
"""

import uuid
import secrets
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import IntegrityError

from src.models.team import Team, TeamRecruitmentStatus
from src.models.team_member import TeamMember, TeamRole
from src.models.player import Player
from src.models.message import Message
from src.models.sector import Sector
from src.models.fleet import Fleet, FleetStatus
from src.models.treasury_transaction import TreasuryTransaction
from src.services.audit_service import AuditService, AuditAction

logger = logging.getLogger(__name__)

# Treasury resources a Player row can actually hold as real columns. The Player
# model has no commodity columns (fuel/organics/equipment/... live in ship
# cargo), so a setattr for those would write a transient Python attribute that
# evaporates at session end — silently destroying the resources on either a
# deposit (player debit lost) or a withdraw/transfer (player credit lost).
#
# Both directions use this same whitelist so nothing becomes a one-way sink:
# a resource you can deposit must be one a player can later retrieve, and vice
# versa. Only columns confirmed to exist on BOTH Player (as `<resource>`) and
# Team (as `treasury_<resource>`) belong here — currently credits and
# quantum_crystals.
PLAYER_TRANSFERABLE_RESOURCES = {"credits", "quantum_crystals"}

# Honest, human-readable list of what a player may move to/from the treasury,
# reused across deposit/withdraw/transfer so the error message can never drift
# out of sync with the whitelist above.
_TRANSFERABLE_LABEL = " and ".join(sorted(PLAYER_TRANSFERABLE_RESOURCES))


class TeamService:
    def __init__(self, db: Session):
        self.db = db
        self.audit_service = AuditService(db)

    def _send_notification(
        self,
        sender_id,
        recipient_id=None,
        team_id=None,
        subject=None,
        content="",
        priority="normal",
        **kwargs
    ):
        """Send a notification message synchronously via direct DB insert.

        This bypasses MessageService (which is async) so that sync TeamService
        methods can fire notifications without needing await.
        Extra kwargs (e.g. message_type) are silently absorbed for compatibility.
        """
        from uuid import uuid4
        try:
            msg = Message(
                sender_id=sender_id,
                recipient_id=recipient_id,
                team_id=team_id,
                subject=subject,
                content=content,
                message_type=kwargs.get("message_type", "team" if team_id else "player"),
                priority=priority,
                thread_id=uuid4(),
            )
            self.db.add(msg)
            # Don't commit here -- let the caller's commit handle it
        except Exception as e:
            logger.warning(f"Failed to send team notification: {e}")
    
    def create_team(self, creator_id: uuid.UUID, name: str, description: str = None, tag: str = None,
                   max_members: int = 4, recruitment_status: str = TeamRecruitmentStatus.OPEN.value) -> Team:
        """Create a new team with the creator as leader"""
        # Check if player already has a team.
        # Locks the creator row to prevent concurrent create/join race
        # conditions (same lock pattern as ship_upgrades purchases).
        creator = self.db.query(Player).filter(Player.id == creator_id).populate_existing().with_for_update().first()
        if not creator:
            raise ValueError("Player not found")
        
        if creator.team_id:
            raise ValueError("Player is already in a team")
        
        # Check if team name is unique
        existing_team = self.db.query(Team).filter(Team.name == name).first()
        if existing_team:
            raise ValueError("Team name already taken")

        # Charge team creation cost
        TEAM_CREATION_COST = 10000
        if creator.credits < TEAM_CREATION_COST:
            raise ValueError(f"Need {TEAM_CREATION_COST} credits to create a team (have {creator.credits})")
        creator.credits -= TEAM_CREATION_COST

        # Create the team
        team = Team(
            name=name,
            description=description,
            tag=tag,
            leader_id=creator_id,
            max_members=max_members,
            recruitment_status=recruitment_status
        )
        
        self.db.add(team)
        try:
            self.db.flush()
        except IntegrityError:
            # Two creators raced past the SELECT-based name check above —
            # the unique constraint is the backstop. Roll back the aborted
            # transaction and surface the same clean ValueError the route
            # layer already maps to a 400.
            self.db.rollback()
            raise ValueError("Team name already taken")
        
        # Create team member entry for the leader
        team_member = TeamMember(
            team_id=team.id,
            player_id=creator_id,
            role=TeamRole.LEADER.value,
            can_invite=True,
            can_kick=True,
            can_manage_treasury=True,
            can_manage_missions=True,
            can_manage_alliances=True
        )
        self.db.add(team_member)
        
        # Update player's team_id
        creator.team_id = team.id
        
        # Audit log
        self.audit_service.log_action(
            user_id=creator_id,
            action=AuditAction.CREATE,
            resource_type="team",
            resource_id=str(team.id),
            details={"team_name": name, "team_id": str(team.id)}
        )

        # Snapshot as plain strs BEFORE commit — ORM attributes expire on
        # commit, but the WS room-hop fires AFTER (WO-RT-ROOM-HOP). Not in
        # the WO's original enumeration (join_team / remove_member /
        # leave_team), but creator.team_id is the identical None->team.id
        # transition join_team makes — skipping it would leave a team's own
        # creator failing the ~:921 team-chat revalidation until reconnect.
        hop_user_id = str(creator.user_id)
        hop_team_id = str(team.id)

        self.db.commit()
        self._schedule_team_hop(hop_user_id, hop_team_id)
        return team
    
    def get_team(self, team_id: uuid.UUID) -> Optional[Team]:
        """Get team by ID"""
        return self.db.query(Team).filter(Team.id == team_id).first()
    
    def update_team(self, team_id: uuid.UUID, player_id: uuid.UUID, **kwargs) -> Team:
        """Update team details (leader/officer only)"""
        team = self.get_team(team_id)
        if not team:
            raise ValueError("Team not found")
        
        # Check permissions
        member = self._get_team_member(team_id, player_id)
        if not member or member.role not in [TeamRole.LEADER.value, TeamRole.OFFICER.value]:
            raise ValueError("Insufficient permissions")
        
        # Update allowed fields
        allowed_fields = ['description', 'tag', 'logo', 'recruitment_status', 'max_members', 
                         'join_requirements', 'resource_sharing']
        
        for field, value in kwargs.items():
            if field in allowed_fields:
                setattr(team, field, value)
        
        # Audit log
        self.audit_service.log_action(
            user_id=player_id,
            action=AuditAction.UPDATE,
            resource_type="team",
            resource_id=str(team_id),
            details={"updates": kwargs}
        )
        
        self.db.commit()
        return team
    
    def delete_team(self, team_id: uuid.UUID, player_id: uuid.UUID) -> bool:
        """Delete team (leader only)"""
        team = self.get_team(team_id)
        if not team:
            raise ValueError("Team not found")

        # Cheap, UNLOCKED early reject (WO-TEAM-DELETE-LOCKORDER+DOUBLECLICK-
        # REVISE -- both cipher MEDIUM and mack HIGH converged on this same
        # fix). The route this feeds, DELETE /teams/{team_id}
        # (api/routes/teams.py), takes an arbitrary team_id from ANY
        # authenticated player with no membership/leader pre-check of its
        # own -- delete_team is the only gate. Without an early reject here,
        # a non-leader (or non-member entirely) forces a SELECT ... FOR
        # UPDATE on every one of the target team's Fleet rows, PLUS the Team
        # row itself, before ever reaching the leader check -- a spammable
        # (the rate limiter is dead code) griefing/contention lever against
        # a rival team's mid-battle fleet ops, and a straight regression
        # from the pre-fix code, which rejected non-leaders before any
        # locking at all. This reads off the unlocked top-of-function
        # get_team() -- an optimistic filter, not the authoritative
        # decision -- so a leader_id that's stale by the time this runs
        # (a concurrent transfer_leadership mid-flight) simply falls through
        # to the Fleet lock path same as a real leader would; the LOCKED
        # re-check below (off the populate_existing() Team re-read) remains
        # the sole source of truth and still closes that TOCTOU. Two checks,
        # not one moved: this one is cheap-and-optimistic, that one is
        # authoritative-and-locked.
        if team.leader_id != player_id:
            raise ValueError("Only team leader can delete the team")

        # Lock the team's Fleet rows ascending-by-id (mirrors fleet_service's
        # own _lock_fleets_ascending idiom exactly -- WO-TEAM-DELETE-FLEET-
        # GUARD) BEFORE any other mutation, making this the FIRST lock this
        # method acquires (Fleet-before-everything). team_service.py has
        # never imported fleet_service, and reaching across services for one
        # locking helper would be a layering smell this codebase doesn't
        # otherwise have, so the loop is replicated verbatim rather than
        # borrowed off a FleetService instance. No caller anywhere locks
        # Team-then-Fleet, so this can't introduce a new AB-BA; the fleet
        # side of fleet_service.py's documented "FleetBattle -> Fleet ->
        # Team -> Player" convention is honored for this new edge.
        #
        # Closes a griefing exploit, not just an accidental race: without
        # this, a losing team's leader could delete their own team WHILE
        # their fleet is IN_BATTLE (delete_team never checked Fleet.status
        # at all) -- the cascade silently deletes the Fleet row
        # (Fleet.team_id is ON DELETE CASCADE), SET-NULLing the live
        # FleetBattle's attacker_fleet_id/defender_fleet_id (that FK is ON
        # DELETE SET NULL, not RESTRICT), permanently orphaning the battle
        # and crashing the surviving player's next round-simulate call with
        # an uncaught AttributeError. Locking here mirrors disband_fleet's
        # own guard (fleet_service.py:446-447) and closes the TOCTOU in both
        # interleavings: if this lock wins first, a concurrent
        # initiate_battle blocks on it and -- once this transaction commits
        # -- re-selects to find the fleet gone, raising its own clean
        # "Invalid fleet IDs"; if initiate_battle's lock wins first, this
        # call blocks until it commits, then re-reads status == IN_BATTLE
        # and rejects cleanly below.
        team_fleet_ids = sorted(
            fleet.id for fleet in
            self.db.query(Fleet).filter(Fleet.team_id == team_id).all()
        )
        for fid in team_fleet_ids:
            fleet = (
                self.db.query(Fleet)
                .filter(Fleet.id == fid)
                .populate_existing()
                .with_for_update()
                .first()
            )
            # Short-circuit (mack LOW): raise on the FIRST in-battle fleet
            # instead of locking every remaining fleet just to run any()
            # over a fully-materialized list afterward -- no behavior
            # change, strictly less lock-hold time on a team with several
            # fleets.
            if fleet is not None and fleet.status == FleetStatus.IN_BATTLE.value:
                raise ValueError("Cannot delete team while a fleet is in an active battle")

        # TOCTOU close (mack HIGH): the .all() gather above is an UNLOCKED,
        # point-in-time snapshot taken BEFORE any lock is held -- a fleet
        # CREATED for this team after that snapshot was never in
        # team_fleet_ids, so the loop above never locked or checked it, and
        # it could still enter IN_BATTLE before this method's own commit.
        # Close that window with one more query, scoped directly to
        # team_id + status == IN_BATTLE rather than an id list, placed as
        # late as possible before the irreversible cascade (nothing between
        # here and db.delete(team) below touches Fleet, so nothing can
        # invalidate this check in between). with_for_update() here (not a
        # bare .first()) means a concurrent initiate_battle mid-flight on
        # this exact fleet BLOCKS this call rather than racing past it --
        # once unblocked, this re-evaluates against the now-committed
        # truth, same as the per-row loop above already does for fleets it
        # knew about.
        still_in_battle = (
            self.db.query(Fleet)
            .filter(Fleet.team_id == team_id, Fleet.status == FleetStatus.IN_BATTLE.value)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if still_in_battle is not None:
            raise ValueError("Cannot delete team while a fleet is in an active battle")

        # Lock the Team row itself, FOR UPDATE, right here -- AFTER the
        # Fleet lock/recheck above, BEFORE the Player bulk-update below
        # (WO-TEAM-DELETE-LOCKORDER + WO-TEAM-DELETE-DOUBLECLICK). Without
        # this, the method's actual lock order was Fleet -> Player (the
        # bulk UPDATE two lines down row-locks every member) -> Team (only
        # locked implicitly, at flush, by db.delete(team) far below) --
        # reversing the "resource-before-player" convention this same
        # method (and deposit_to_treasury/withdraw_from_treasury, both
        # `Team` FOR UPDATE before `Player` FOR UPDATE) otherwise holds
        # everywhere else in this file. That reversal is a latent AB-BA
        # against any concurrent Team-then-Player locker (e.g.
        # deposit_to_treasury targeting one of this team's members).
        # Inserting the lock here restores Fleet -> Team -> Player.
        #
        # This lock ALSO closes the double-click race, though which line
        # the two concurrent calls actually serialize AT depends on whether
        # the team owns a fleet: two concurrent delete_team calls both pass
        # the UNLOCKED not-found check above (team.id still exists, neither
        # has committed yet), and both pass the UNLOCKED early leader-check
        # above that (same reasoning -- neither has committed). For a
        # ZERO-FLEET team, both then sail straight through the Fleet-lock
        # loop/recheck (nothing to lock) and serialize HERE, at the Team
        # lock. For a FLEET-OWNING team, they instead serialize EARLIER, at
        # the per-fleet `SELECT ... FOR UPDATE` in the loop above (or its
        # team-scoped IN_BATTLE recheck) -- the loser blocks on that Fleet
        # row lock until the winner's commit, at which point the winner's
        # own `db.delete(team)` has already cascade-deleted the fleet
        # (Fleet.team_id is ON DELETE CASCADE), so the loser's re-select by
        # id comes back None, the loop/recheck simply find nothing to flag,
        # and the loser falls through to THIS Team lock only to find the
        # Team row gone too. Either way the end-state is identical: the
        # loser's locked re-read (Fleet's or Team's, whichever it blocks on
        # first) finds no row, `.first()` returns None, and this raises a
        # clean "Team not found" below -- not the old behavior, where the
        # loser sailed on to its own `db.delete(team)` on an already-0-row
        # PK, tripping SQLAlchemy's `confirm_deleted_rows` into a
        # `StaleDataError` at commit -- uncaught by the existing `except
        # IntegrityError` backstop below, surfacing as a raw 500.
        #
        # populate_existing() is load-bearing, not decorative: `team` is
        # ALREADY in this Session's identity map from the unlocked
        # get_team() read at the top of this method. Without
        # populate_existing(), this query still issues the SELECT ... FOR
        # UPDATE and still takes the DB-level row lock, but SQLAlchemy
        # would hand back that SAME cached Python object with whatever
        # leader_id it had at the top-of-function read, NOT a refresh from
        # the row it just locked -- so the leader-check right below would
        # silently run against stale data despite correctly holding the
        # lock. Trace: nothing between that top read and this line touches
        # the Team row (the fleet-gather/recheck above only read/lock
        # Fleet rows), so no prior flush is needed here -- the
        # identity-map staleness is the only reason populate_existing() is
        # required.
        team = (
            self.db.query(Team)
            .filter(Team.id == team_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if team is None:
            raise ValueError("Team not found")

        # Check if player is leader -- the SECOND, AUTHORITATIVE check
        # (the first, cheap-and-optimistic one already ran at the very top,
        # before any locking, purely to reject the common non-leader case
        # without paying for the Fleet locks -- see the comment there). This
        # one re-validates against the now-locked, freshly-refreshed row so
        # a non-leader still gets a clean 403 either way, but a concurrent
        # transfer_leadership (team_service.py's other `team.leader_id =
        # ...` writer) that committed in the gap between the top-of-function
        # read and this lock can't leave a stale leader_id sneaking a
        # now-ex-leader's delete through.
        if team.leader_id != player_id:
            raise ValueError("Only team leader can delete the team")

        # Remove all members' team_id
        self.db.query(Player).filter(Player.team_id == team_id).update({"team_id": None})

        # Relinquish sector control. Sector.controlling_team_id is a "who
        # currently holds this" pointer, not team-owned data, but its FK
        # (unlike Player.team_id / Drone.team_id / PirateKillLog.attacker_team_id
        # / CargoWreck.original_team_id, all ON DELETE SET NULL) has no
        # ondelete action -- left unhandled it raises an IntegrityError on
        # delete. A disbanding team releases its claims same as a leaving
        # member releases theirs above.
        self.db.query(Sector).filter(Sector.controlling_team_id == team_id).update(
            {"controlling_team_id": None}
        )

        # Detach (don't delete) team chat history. Message.team_id has no
        # ondelete action either, but message rows are audit-trail data --
        # messaging.md: "Moderated messages remain in the database ... even
        # after content removal" -- so nulling team_id clears the FK while
        # keeping the rows (sender, content, moderation stamps) intact,
        # same as a direct message with no live recipient still exists.
        self.db.query(Message).filter(Message.team_id == team_id).update(
            {"team_id": None}
        )

        # Delete the team. team_members / TeamReputation / Fleet rows all
        # cascade via the Team model's own cascade="all, delete-orphan"
        # relationships (and are mirrored by an ON DELETE CASCADE at the DB
        # level); Drone.team_id / PirateKillLog.attacker_team_id /
        # CargoWreck.original_team_id null out via ON DELETE SET NULL.
        self.db.delete(team)

        # Audit log
        self.audit_service.log_action(
            user_id=player_id,
            action=AuditAction.DELETE,
            resource_type="team",
            resource_id=str(team_id),
            details={"team_name": team.name}
        )

        try:
            self.db.commit()
        except IntegrityError as e:
            # Belt-and-suspenders: any dependency we haven't accounted for
            # above surfaces as a clean 4xx instead of an uncaught 500.
            self.db.rollback()
            logger.error("delete_team %s hit an unhandled FK dependency: %s", team_id, e)
            raise ValueError(
                "Cannot delete team: it still has dependent records that must be resolved first"
            ) from e
        return True
    
    def get_team_members(self, team_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Get all team members with their details"""
        members = (
            self.db.query(TeamMember, Player)
            .join(Player, TeamMember.player_id == Player.id)
            .filter(TeamMember.team_id == team_id)
            .order_by(TeamMember.joined_at)
            .all()
        )
        
        return [{
            "player_id": str(member.player_id),
            # nickname is nullable — fall back to the Player.username
            # property (nickname -> user.username -> "Unknown Player")
            "nickname": player.nickname or player.username,
            "role": member.role,
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "last_active": member.last_active.isoformat() if member.last_active else None,
            "can_invite": member.can_invite,
            "can_kick": member.can_kick,
            "can_manage_treasury": member.can_manage_treasury,
            "can_manage_missions": member.can_manage_missions,
            "can_manage_alliances": member.can_manage_alliances,
            "contribution_credits": member.contribution_credits,
            "current_sector": player.current_sector_id,
            # canon gap: no per-player combat rating exists yet
            # (Team.combat_rating is the team aggregate)
            "combat_rating": 0.0
        } for member, player in members]
    
    def invite_player(self, team_id: uuid.UUID, inviter_id: uuid.UUID, 
                     player_nickname: str) -> Dict[str, Any]:
        """Invite a player to the team"""
        # Get team and check permissions
        team = self.get_team(team_id)
        if not team:
            raise ValueError("Team not found")
        
        member = self._get_team_member(team_id, inviter_id)
        if not member or not member.can_invite:
            raise ValueError("Insufficient permissions to invite")
        
        # Check if team is full
        member_count = self.db.query(TeamMember).filter(TeamMember.team_id == team_id).count()
        if member_count >= team.max_members:
            raise ValueError("Team is full")
        
        # Find target player
        target_player = self.db.query(Player).filter(Player.nickname == player_nickname).first()
        if not target_player:
            raise ValueError("Player not found")
        
        if target_player.team_id:
            raise ValueError("Player is already in a team")
        
        # Generate invitation code
        invitation_code = secrets.token_urlsafe(16)
        
        # Store invitation in team's invitation_codes
        if not team.invitation_codes:
            team.invitation_codes = []
        
        team.invitation_codes.append({
            "code": invitation_code,
            "player_id": str(target_player.id),
            "invited_by": str(inviter_id),
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat()
        })
        
        # Send invitation message
        inviter = self.db.query(Player).filter(Player.id == inviter_id).first()
        self._send_notification(
            sender_id=inviter_id,
            recipient_id=target_player.id,
            subject=f"Team Invitation: {team.name}",
            content=f"{inviter.nickname} has invited you to join team '{team.name}'.\n\n"
                   f"Team Description: {team.description or 'No description'}\n"
                   f"Members: {member_count}/{team.max_members}\n\n"
                   f"To accept this invitation, use the team join command with code: {invitation_code}",
            message_type="system",
            priority="high"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=inviter_id,
            action=AuditAction.UPDATE,  # was "team.invite"
            resource_type="team",
            resource_id=team_id,
            details={
                "invited_player": player_nickname,
                "invited_player_id": str(target_player.id)
            }
        )
        
        self.db.commit()
        
        return {
            "invitation_code": invitation_code,
            "invited_player": player_nickname,
            "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat()
        }
    
    def join_team(self, player_id: uuid.UUID, team_id: uuid.UUID = None, 
                  invitation_code: str = None) -> Team:
        """Join a team (via direct join for open teams or invitation code)"""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise ValueError("Player not found")
        
        if player.team_id:
            raise ValueError("Player is already in a team")
        
        team = None
        
        # Join via invitation code
        if invitation_code:
            # Find team with this invitation code
            teams = self.db.query(Team).all()
            for t in teams:
                if t.invitation_codes:
                    for invite in t.invitation_codes:
                        if invite["code"] == invitation_code:
                            # Check if invitation is for this player
                            if invite["player_id"] != str(player_id):
                                raise ValueError("This invitation is not for you")
                            
                            # Check if invitation is expired
                            expires_at = datetime.fromisoformat(invite["expires_at"])
                            if datetime.utcnow() > expires_at:
                                raise ValueError("Invitation has expired")
                            
                            team = t
                            # Remove the used invitation
                            t.invitation_codes.remove(invite)
                            break
                    
                    if team:
                        break
            
            if not team:
                raise ValueError("Invalid invitation code")
        
        # Direct join for open teams
        elif team_id:
            team = self.get_team(team_id)
            if not team:
                raise ValueError("Team not found")
            
            if team.recruitment_status != TeamRecruitmentStatus.OPEN.value:
                raise ValueError("Team is not open for direct joining")
        
        else:
            raise ValueError("Either team_id or invitation_code is required")
        
        # Check if team is full
        member_count = self.db.query(TeamMember).filter(TeamMember.team_id == team.id).count()
        if member_count >= team.max_members:
            raise ValueError("Team is full")
        
        # Create team member entry
        team_member = TeamMember(
            team_id=team.id,
            player_id=player_id,
            role=TeamRole.MEMBER.value
        )
        self.db.add(team_member)
        
        # Update player's team_id
        player.team_id = team.id
        
        # Send welcome message to team
        self._send_notification(
            sender_id=player_id,
            team_id=team.id,
            subject="New Team Member",
            content=f"{player.nickname} has joined the team!",
            message_type="team"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=player_id,
            action=AuditAction.UPDATE,  # was "team.join"
            resource_type="team",
            resource_id=team.id,
            details={"team_name": team.name, "method": "invitation" if invitation_code else "direct"}
        )

        # Snapshot as plain strs BEFORE commit — ORM attributes expire on
        # commit, but the WS room-hop fires AFTER (WO-RT-ROOM-HOP).
        hop_user_id = str(player.user_id)
        hop_team_id = str(team.id)
        team_name = team.name

        # ARIA narration — P-A3 team join, Player.team_id null→non-null
        # (aria-companion.md:228, WO-ARIA-NARRATE-KERNEL). Scoped to THIS
        # method only (the canonical "join" action) — not the separate
        # create_team path, which is a distinct action from "joining" per
        # the event's own name. Dedupe key is the team id: a player who
        # leaves and later rejoins the SAME team is not re-narrated;
        # joining a DIFFERENT team narrates again. Best-effort, never
        # fails the join.
        try:
            from src.services.aria_narration_service import (
                dispatch_narration_push,
                get_aria_narration_service,
                resolve_assistance_level,
            )
            narration_line = get_aria_narration_service().record_event(
                "P-A3",
                player_id,
                assistance_level=resolve_assistance_level(self.db, player_id),
                dedupe_key=hop_team_id,
                context={"team_name": team_name},
            )
            # `player`, not `player_id`: dispatch_narration_push reads
            # .user_id synchronously (before commit expires it) — reuses
            # the same still-live ORM object hop_user_id above snapshotted.
            if narration_line is not None and narration_line.delivered_immediately:
                dispatch_narration_push(player, narration_line)
        except Exception as e:
            logger.error("ARIA narration hook failed (P-A3): %s", e)

        self.db.commit()
        self._schedule_team_hop(hop_user_id, hop_team_id)
        return team
    
    def remove_member(self, team_id: uuid.UUID, actor_id: uuid.UUID, 
                      member_id: uuid.UUID) -> bool:
        """Remove a member from the team"""
        team = self.get_team(team_id)
        if not team:
            raise ValueError("Team not found")
        
        # Check permissions
        actor_member = self._get_team_member(team_id, actor_id)
        if not actor_member or not actor_member.can_kick:
            raise ValueError("Insufficient permissions to remove members")
        
        # Get target member
        target_member = self._get_team_member(team_id, member_id)
        if not target_member:
            raise ValueError("Member not found in team")
        
        # Cannot kick the leader
        if target_member.role == TeamRole.LEADER.value:
            raise ValueError("Cannot remove the team leader")
        
        # Officers can only kick members and recruits
        if actor_member.role == TeamRole.OFFICER.value and \
           target_member.role == TeamRole.OFFICER.value:
            raise ValueError("Officers cannot remove other officers")
        
        # Remove member
        self.db.delete(target_member)
        
        # Update player's team_id
        player = self.db.query(Player).filter(Player.id == member_id).first()
        hop_user_id = str(player.user_id) if player else None
        if player:
            player.team_id = None

        # Send notification
        self._send_notification(
            sender_id=actor_id,
            recipient_id=member_id,
            subject="Removed from Team",
            content=f"You have been removed from team '{team.name}'.",
            message_type="system",
            priority="high"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=actor_id,
            action=AuditAction.UPDATE,  # was "team.remove_member"
            resource_type="team",
            resource_id=team_id,
            details={"removed_member_id": str(member_id)}
        )

        self.db.commit()
        if hop_user_id is not None:
            self._schedule_team_hop(hop_user_id, None)
        return True

    def leave_team(self, player_id: uuid.UUID) -> bool:
        """Leave the current team"""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player or not player.team_id:
            raise ValueError("Player is not in a team")
        
        team = self.get_team(player.team_id)
        member = self._get_team_member(player.team_id, player_id)
        
        if not member:
            raise ValueError("Member record not found")
        
        # If leader is leaving, transfer leadership or disband
        if member.role == TeamRole.LEADER.value:
            # Find another officer or member to promote
            new_leader = (
                self.db.query(TeamMember)
                .filter(
                    TeamMember.team_id == team.id,
                    TeamMember.player_id != player_id
                )
                .order_by(
                    # Prefer officers (desc puts the True matches first),
                    # then by join date
                    (TeamMember.role == TeamRole.OFFICER.value).desc(),
                    TeamMember.joined_at
                )
                .first()
            )
            
            if new_leader:
                # Transfer leadership
                new_leader.role = TeamRole.LEADER.value
                new_leader.can_invite = True
                new_leader.can_kick = True
                new_leader.can_manage_treasury = True
                new_leader.can_manage_missions = True
                new_leader.can_manage_alliances = True
                team.leader_id = new_leader.player_id
                
                # Notify new leader
                self._send_notification(
                    sender_id=player_id,
                    recipient_id=new_leader.player_id,
                    subject="Team Leadership Transferred",
                    content=f"You are now the leader of team '{team.name}'.",
                    message_type="system",
                    priority="urgent"
                )
            else:
                # No other members -- disband via delete_team (WO-TEAM-
                # DELETE-FLEET-GUARD-REVISE part 2, cipher CONFIRMED HIGH).
                # This branch used to call db.delete(team) directly: an
                # unguarded duplicate of the exact griefing exploit
                # delete_team was hardened against (no Fleet IN_BATTLE
                # check at all), and it never nulled
                # Sector.controlling_team_id / Message.team_id either (the
                # raw-500 bug WO-TEAM-DELETE-GUARD fixed only in
                # delete_team). Routing through delete_team ports its FULL
                # hardening -- the Fleet IN_BATTLE guard + Part-1 TOCTOU
                # close, Sector/Message cleanup, IntegrityError backstop,
                # its own DELETE audit log -- atomically, inside
                # delete_team's own commit.
                #
                # delete_team's leader-check (team.leader_id != player_id)
                # is satisfied here: this branch is reached only when THIS
                # player IS team.leader_id, and it is never reassigned
                # except in the new_leader branch above.
                #
                # delete_team commits internally, and its own cascade
                # already (1) deletes this player's OWN TeamMember row
                # (Team.team_members is cascade="all, delete-orphan" --
                # the SAME row this method's own self.db.delete(member)
                # below would otherwise re-target) and (2) nulls this
                # player's OWN Player.team_id (the bulk
                # Player.team_id==team_id update matches them -- they are
                # the team's ONLY remaining member, which is exactly why
                # this branch was reached). Re-running this method's own
                # generic member-delete / player.team_id-null / "Member
                # Left" broadcast / "team.leave" audit log below would (a)
                # be pure no-op busywork -- there is no other member left
                # to notify -- and (b) risk touching the `team`/`member`
                # objects AFTER delete_team's own commit already
                # expired/cascaded them out from under this session. So
                # this is a clean early return instead: capture the
                # room-hop id BEFORE calling delete_team (same
                # expire-on-commit rationale as the snapshot below), let
                # delete_team run (a raised ValueError -- e.g. the
                # IN_BATTLE guard -- propagates straight out exactly like
                # any other delete_team caller, and nothing in this method
                # has mutated anything yet), then hop and return.
                hop_user_id = str(player.user_id)
                self.delete_team(team.id, player_id)
                self._schedule_team_hop(hop_user_id, None)
                return True

        # Remove member record
        self.db.delete(member)

        # Update player's team_id
        player.team_id = None
        # Snapshot BEFORE commit — ORM attributes expire on commit, but the
        # WS room-hop fires AFTER (WO-RT-ROOM-HOP).
        hop_user_id = str(player.user_id)

        # Notify team
        if team:
            self._send_notification(
                sender_id=player_id,
                team_id=team.id,
                subject="Member Left",
                content=f"{player.nickname} has left the team.",
                message_type="team"
            )
        
        # Audit log
        self.audit_service.log_action(
            user_id=player_id,
            action=AuditAction.UPDATE,  # was "team.leave"
            resource_type="team",
            resource_id=team.id if team else None,
            details={"team_name": team.name if team else "disbanded"}
        )

        self.db.commit()
        self._schedule_team_hop(hop_user_id, None)
        return True

    def update_member_role(self, team_id: uuid.UUID, actor_id: uuid.UUID,
                          member_id: uuid.UUID, new_role: str) -> TeamMember:
        """Update a member's role in the team"""
        team = self.get_team(team_id)
        if not team:
            raise ValueError("Team not found")
        
        # Only leader can change roles
        if team.leader_id != actor_id:
            raise ValueError("Only team leader can change member roles")
        
        # Get target member
        member = self._get_team_member(team_id, member_id)
        if not member:
            raise ValueError("Member not found in team")
        
        # Cannot change own role
        if actor_id == member_id:
            raise ValueError("Cannot change your own role")
        
        # Validate role
        if new_role not in [r.value for r in TeamRole]:
            raise ValueError("Invalid role")
        
        # Cannot have multiple leaders
        if new_role == TeamRole.LEADER.value:
            raise ValueError("Cannot have multiple leaders. Transfer leadership instead.")
        
        # Update role and permissions
        member.role = new_role
        
        # Set permissions based on role
        if new_role == TeamRole.OFFICER.value:
            member.can_invite = True
            member.can_kick = True
            member.can_manage_missions = True
        elif new_role == TeamRole.MEMBER.value:
            member.can_invite = False
            member.can_kick = False
            member.can_manage_missions = False
            member.can_manage_treasury = False
            member.can_manage_alliances = False
        elif new_role == TeamRole.RECRUIT.value:
            member.can_invite = False
            member.can_kick = False
            member.can_manage_missions = False
            member.can_manage_treasury = False
            member.can_manage_alliances = False
        
        # Notify member
        player = self.db.query(Player).filter(Player.id == member_id).first()
        self._send_notification(
            sender_id=actor_id,
            recipient_id=member_id,
            subject="Role Updated",
            content=f"Your role in team '{team.name}' has been changed to {new_role}.",
            message_type="system"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=actor_id,
            action=AuditAction.UPDATE,  # was "team.update_role"
            resource_type="team",
            resource_id=team_id,
            details={
                "member_id": str(member_id),
                "new_role": new_role,
                "member_nickname": player.nickname if player else "Unknown"
            }
        )
        
        self.db.commit()
        return member
    
    def get_user_permissions(self, team_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, bool]:
        """Get a player's permissions in the team"""
        member = self._get_team_member(team_id, player_id)
        if not member:
            return {
                "can_invite": False,
                "can_kick": False,
                "can_manage_treasury": False,
                "can_manage_missions": False,
                "can_manage_alliances": False,
                "is_member": False,
                "role": None
            }
        
        return {
            "can_invite": member.can_invite,
            "can_kick": member.can_kick,
            "can_manage_treasury": member.can_manage_treasury,
            "can_manage_missions": member.can_manage_missions,
            "can_manage_alliances": member.can_manage_alliances,
            "is_member": True,
            "role": member.role
        }
    
    def _get_team_member(self, team_id: uuid.UUID, player_id: uuid.UUID) -> Optional[TeamMember]:
        """Get a team member record"""
        return (
            self.db.query(TeamMember)
            .filter(
                TeamMember.team_id == team_id,
                TeamMember.player_id == player_id
            )
            .first()
        )

    @staticmethod
    def _schedule_team_hop(user_id: str, new_team_id: Optional[str]) -> None:
        """Best-effort WS team-room hop after a membership change commits
        (WO-RT-ROOM-HOP). Keeps ConnectionManager.team_connections — and so
        both team-chat delivery and the revalidation gate in
        handle_websocket_message's "team" branch — correct without requiring
        a reconnect: a kicked/left member immediately stops receiving AND
        sending team chat, a joined member immediately starts.

        Mirrors movement_service._broadcast_sector_presence /
        hangar_service._schedule_region_hop: import inside the function, grab
        the running loop, schedule connection_manager.update_user_team with
        loop.create_task (so it runs after the caller's commit and never
        blocks the sync team transaction), and swallow any failure (no loop,
        no socket) so a quiet socket can never break a team operation."""
        try:
            import asyncio
            from src.services.websocket_service import connection_manager

            loop = asyncio.get_running_loop()
            loop.create_task(connection_manager.update_user_team(user_id, new_team_id))
        except Exception:
            logger.debug(
                "Skipped team WS room-hop (no loop or socket)",
                exc_info=True,
            )

    def transfer_leadership(self, team_id: uuid.UUID, current_leader_id: uuid.UUID,
                           new_leader_id: uuid.UUID) -> Team:
        """Transfer team leadership to another member"""
        team = self.get_team(team_id)
        if not team:
            raise ValueError("Team not found")
        
        # Verify current leader
        if team.leader_id != current_leader_id:
            raise ValueError("Only current leader can transfer leadership")
        
        # Get both members
        current_leader = self._get_team_member(team_id, current_leader_id)
        new_leader = self._get_team_member(team_id, new_leader_id)
        
        if not new_leader:
            raise ValueError("New leader is not a team member")
        
        # Update team leader
        team.leader_id = new_leader_id
        
        # Update roles
        current_leader.role = TeamRole.OFFICER.value
        new_leader.role = TeamRole.LEADER.value
        
        # Update permissions
        new_leader.can_invite = True
        new_leader.can_kick = True
        new_leader.can_manage_treasury = True
        new_leader.can_manage_missions = True
        new_leader.can_manage_alliances = True
        
        # Send notifications
        self._send_notification(
            sender_id=current_leader_id,
            team_id=team_id,
            subject="Leadership Transferred",
            content=f"Leadership has been transferred to {self.db.query(Player).filter(Player.id == new_leader_id).first().nickname}.",
            message_type="team",
            priority="high"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=current_leader_id,
            action=AuditAction.UPDATE,  # was "team.transfer_leadership"
            resource_type="team",
            resource_id=team_id,
            details={
                "new_leader_id": str(new_leader_id),
                "team_name": team.name
            }
        )
        
        self.db.commit()
        return team
    
    # Treasury Management Methods

    def _record_treasury_transaction(
        self,
        team_id: uuid.UUID,
        resource_type: str,
        kind: str,
        amount: int,
        balance_after: int,
        actor_player_id: Optional[uuid.UUID],
        reason: Optional[str] = None,
    ) -> None:
        """Append one TreasuryTransaction ledger row for a treasury mutation.

        Single-writer: called by each treasury mutation method AFTER the balance
        is mutated but BEFORE the shared commit, so the ledger row lands in the
        SAME transaction as the balance change (atomic — no orphan rows, no
        missed events). The row is staged here; the caller's self.db.commit()
        persists it together with the balance.
        """
        entry = TreasuryTransaction(
            team_id=team_id,
            resource_type=resource_type,
            kind=kind,
            amount=amount,
            balance_after=balance_after,
            actor_player_id=actor_player_id,
            reason=reason,
        )
        self.db.add(entry)

    def deposit_to_treasury(self, team_id: uuid.UUID, player_id: uuid.UUID,
                           resource_type: str, amount: int) -> Dict[str, Any]:
        """Deposit resources to team treasury"""
        # Lock team and player rows to prevent race conditions
        team = self.db.query(Team).filter(Team.id == team_id).with_for_update().first()
        if not team:
            raise ValueError("Team not found")

        # Check if player is a member
        member = self._get_team_member(team_id, player_id)
        if not member:
            raise ValueError("Player is not a team member")

        # Get player with lock
        player = self.db.query(Player).filter(Player.id == player_id).populate_existing().with_for_update().first()
        if not player:
            raise ValueError("Player not found")
        
        # Validate resource type and amount
        treasury_field = f"treasury_{resource_type}"
        if not hasattr(team, treasury_field):
            raise ValueError(f"Invalid resource type: {resource_type}")

        # Mirror the withdrawal/transfer whitelist: only resources a Player row
        # can actually hold may be deposited. Without this, depositing a
        # commodity would setattr a transient attribute on the player, "debit"
        # nothing real, yet credit the treasury — minting resources from thin
        # air. Same honest message as the outbound path.
        if resource_type not in PLAYER_TRANSFERABLE_RESOURCES:
            raise ValueError(
                f"Players can only deposit {_TRANSFERABLE_LABEL} directly; "
                "commodity transfers require cargo routing — not yet implemented"
            )

        if amount <= 0:
            raise ValueError("Amount must be positive")

        # Check if player has enough resources
        player_resource = getattr(player, resource_type, 0)
        if player_resource < amount:
            raise ValueError(f"Insufficient {resource_type}: have {player_resource}, need {amount}")
        
        # Transfer resources
        setattr(player, resource_type, player_resource - amount)
        current_treasury = getattr(team, treasury_field, 0)
        setattr(team, treasury_field, current_treasury + amount)
        
        # Update member contribution tracking (copy-reassign: in-place JSONB
        # mutation is invisible to SQLAlchemy's change tracking)
        contributions = dict(member.contribution_credits or {})
        contributions[resource_type] = contributions.get(resource_type, 0) + amount
        member.contribution_credits = contributions
        flag_modified(member, "contribution_credits")

        # Ledger: one row per mutation, same txn as the balance change.
        self._record_treasury_transaction(
            team_id=team_id,
            resource_type=resource_type,
            kind=TreasuryTransaction.KIND_DEPOSIT,
            amount=amount,
            balance_after=getattr(team, treasury_field),
            actor_player_id=player_id,
            reason=f"{player.nickname} deposited {amount} {resource_type}",
        )

        # Send team notification
        self._send_notification(
            sender_id=player_id,
            team_id=team_id,
            subject="Treasury Deposit",
            content=f"{player.nickname} deposited {amount} {resource_type} to the team treasury.",
            message_type="team"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=player_id,
            action=AuditAction.UPDATE,  # was "team.treasury.deposit"
            resource_type="team",
            resource_id=team_id,
            details={
                "resource_type": resource_type,
                "amount": amount,
                "new_treasury_balance": getattr(team, treasury_field)
            }
        )
        
        self.db.commit()
        
        return {
            "success": True,
            "resource_type": resource_type,
            "amount_deposited": amount,
            "new_treasury_balance": getattr(team, treasury_field),
            "player_balance": getattr(player, resource_type)
        }
    
    def withdraw_from_treasury(self, team_id: uuid.UUID, player_id: uuid.UUID,
                              resource_type: str, amount: int) -> Dict[str, Any]:
        """Withdraw resources from team treasury"""
        # Lock team and player rows to prevent race conditions
        team = self.db.query(Team).filter(Team.id == team_id).with_for_update().first()
        if not team:
            raise ValueError("Team not found")

        # Check permissions
        member = self._get_team_member(team_id, player_id)
        if not member or not member.can_manage_treasury:
            raise ValueError("Insufficient permissions to manage treasury")

        # Get player with lock
        player = self.db.query(Player).filter(Player.id == player_id).populate_existing().with_for_update().first()
        if not player:
            raise ValueError("Player not found")
        
        # Validate resource type and amount
        treasury_field = f"treasury_{resource_type}"
        if not hasattr(team, treasury_field):
            raise ValueError(f"Invalid resource type: {resource_type}")

        if resource_type not in PLAYER_TRANSFERABLE_RESOURCES:
            raise ValueError(
                f"Players can only receive {_TRANSFERABLE_LABEL} directly; "
                "commodity transfers require cargo routing — not yet implemented"
            )

        if amount <= 0:
            raise ValueError("Amount must be positive")

        # Check if treasury has enough resources
        treasury_balance = getattr(team, treasury_field, 0)
        if treasury_balance < amount:
            raise ValueError(f"Insufficient treasury {resource_type}: have {treasury_balance}, need {amount}")

        # Transfer resources
        setattr(team, treasury_field, treasury_balance - amount)
        player_resource = getattr(player, resource_type, 0)
        setattr(player, resource_type, player_resource + amount)

        # Ledger: one row per mutation, same txn as the balance change.
        self._record_treasury_transaction(
            team_id=team_id,
            resource_type=resource_type,
            kind=TreasuryTransaction.KIND_WITHDRAW,
            amount=amount,
            balance_after=getattr(team, treasury_field),
            actor_player_id=player_id,
            reason=f"{player.nickname} withdrew {amount} {resource_type}",
        )

        # Send team notification
        self._send_notification(
            sender_id=player_id,
            team_id=team_id,
            subject="Treasury Withdrawal",
            content=f"{player.nickname} withdrew {amount} {resource_type} from the team treasury.",
            message_type="team",
            priority="high"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=player_id,
            action=AuditAction.UPDATE,  # was "team.treasury.withdraw"
            resource_type="team",
            resource_id=team_id,
            details={
                "resource_type": resource_type,
                "amount": amount,
                "new_treasury_balance": getattr(team, treasury_field)
            }
        )
        
        self.db.commit()
        
        return {
            "success": True,
            "resource_type": resource_type,
            "amount_withdrawn": amount,
            "new_treasury_balance": getattr(team, treasury_field),
            "player_balance": getattr(player, resource_type)
        }
    
    def transfer_to_player(self, team_id: uuid.UUID, actor_id: uuid.UUID,
                          recipient_nickname: str, resource_type: str, amount: int) -> Dict[str, Any]:
        """Transfer resources from treasury to a specific player"""
        # Lock the team row before reading the treasury balance (mirrors
        # deposit_to_treasury/withdraw_from_treasury; populate_existing()
        # refreshes any already-loaded instance under the lock)
        team = (
            self.db.query(Team)
            .filter(Team.id == team_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not team:
            raise ValueError("Team not found")

        # Check permissions
        actor_member = self._get_team_member(team_id, actor_id)
        if not actor_member or not actor_member.can_manage_treasury:
            raise ValueError("Insufficient permissions to manage treasury")

        # Find recipient (locked — their balance is mutated below)
        recipient = self.db.query(Player).filter(
            Player.nickname == recipient_nickname
        ).populate_existing().with_for_update().first()
        if not recipient:
            raise ValueError("Recipient player not found")
        
        # Check if recipient is a team member
        recipient_member = self._get_team_member(team_id, recipient.id)
        if not recipient_member:
            raise ValueError("Recipient is not a team member")
        
        # Validate resource type and amount
        treasury_field = f"treasury_{resource_type}"
        if not hasattr(team, treasury_field):
            raise ValueError(f"Invalid resource type: {resource_type}")

        if resource_type not in PLAYER_TRANSFERABLE_RESOURCES:
            raise ValueError(
                f"Players can only receive {_TRANSFERABLE_LABEL} directly; "
                "commodity transfers require cargo routing — not yet implemented"
            )

        if amount <= 0:
            raise ValueError("Amount must be positive")

        # Check treasury balance
        treasury_balance = getattr(team, treasury_field, 0)
        if treasury_balance < amount:
            raise ValueError(f"Insufficient treasury {resource_type}: have {treasury_balance}, need {amount}")
        
        # Transfer resources
        setattr(team, treasury_field, treasury_balance - amount)
        recipient_resource = getattr(recipient, resource_type, 0)
        setattr(recipient, resource_type, recipient_resource + amount)
        
        # Send notifications
        actor = self.db.query(Player).filter(Player.id == actor_id).first()

        # Ledger: one row per mutation, same txn as the balance change.
        actor_name = actor.nickname if actor else "Someone"
        self._record_treasury_transaction(
            team_id=team_id,
            resource_type=resource_type,
            kind=TreasuryTransaction.KIND_TRANSFER,
            amount=amount,
            balance_after=getattr(team, treasury_field),
            actor_player_id=actor_id,
            reason=f"{actor_name} transferred {amount} {resource_type} to {recipient.nickname}",
        )

        # Notify recipient
        self._send_notification(
            sender_id=actor_id,
            recipient_id=recipient.id,
            subject="Team Resource Transfer",
            content=f"{actor.nickname} transferred {amount} {resource_type} to you from the team treasury.",
            message_type="system",
            priority="high"
        )
        
        # Notify team
        self._send_notification(
            sender_id=actor_id,
            team_id=team_id,
            subject="Treasury Transfer",
            content=f"{actor.nickname} transferred {amount} {resource_type} from treasury to {recipient.nickname}.",
            message_type="team"
        )
        
        # Audit log
        self.audit_service.log_action(
            user_id=actor_id,
            action=AuditAction.UPDATE,  # was "team.treasury.transfer"
            resource_type="team",
            resource_id=team_id,
            details={
                "recipient": recipient_nickname,
                "recipient_id": str(recipient.id),
                "resource_type": resource_type,
                "amount": amount,
                "new_treasury_balance": getattr(team, treasury_field)
            }
        )
        
        self.db.commit()
        
        return {
            "success": True,
            "recipient": recipient_nickname,
            "resource_type": resource_type,
            "amount_transferred": amount,
            "new_treasury_balance": getattr(team, treasury_field)
        }
    
    def get_treasury_balance(self, team_id: uuid.UUID) -> Dict[str, int]:
        """Get current treasury balance for all resources"""
        team = self.get_team(team_id)
        if not team:
            raise ValueError("Team not found")
        
        return {
            "credits": team.treasury_credits,
            "fuel": team.treasury_fuel,
            "organics": team.treasury_organics,
            "equipment": team.treasury_equipment,
            "technology": team.treasury_technology,
            "luxury_items": team.treasury_luxury_items,
            "precious_metals": team.treasury_precious_metals,
            "raw_materials": team.treasury_raw_materials,
            "plasma": team.treasury_plasma,
            "bio_samples": team.treasury_bio_samples,
            "dark_matter": team.treasury_dark_matter,
            "quantum_crystals": team.treasury_quantum_crystals
        }

    def get_treasury_history(
        self,
        team_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Return the team's treasury ledger, newest-first, paginated.

        Each row is the append-only record written at a mutation site. The actor
        nickname is resolved for display; a deleted actor (SET NULL) renders as
        None so the caller can show "Unknown".
        """
        rows = (
            self.db.query(TreasuryTransaction)
            .filter(TreasuryTransaction.team_id == team_id)
            .order_by(TreasuryTransaction.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        # Resolve actor nicknames in one pass (avoid N+1).
        actor_ids = {r.actor_player_id for r in rows if r.actor_player_id is not None}
        actor_names: Dict[uuid.UUID, str] = {}
        if actor_ids:
            for p in self.db.query(Player).filter(Player.id.in_(actor_ids)).all():
                actor_names[p.id] = p.nickname

        return [
            {
                "id": str(r.id),
                "resource_type": r.resource_type,
                "kind": r.kind,
                "amount": r.amount,
                "balance_after": r.balance_after,
                "actor_player_id": str(r.actor_player_id) if r.actor_player_id else None,
                "actor_name": actor_names.get(r.actor_player_id) if r.actor_player_id else None,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]