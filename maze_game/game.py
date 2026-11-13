"""Core server-side game logic for the multiplayer terminal maze.

The Game class owns the mutable world state and is safe to call from
multiple server threads. Network and terminal concerns belong elsewhere.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from typing import Any, Iterable

try:
    from .maze import Maze, visible_cells
    from .models import Challenge, Pickup, Player, Projectile
    from .spells import COMBOS, COMBO_WINDOW, SPELLS
except ImportError:
    from maze import Maze, visible_cells
    from models import Challenge, Pickup, Player, Projectile
    from spells import COMBOS, COMBO_WINDOW, SPELLS


DEFAULT_WIDTH = 51
DEFAULT_HEIGHT = 25
MAX_PLAYER_NAME_LENGTH = 16
MAX_LOG_ENTRIES = 12
BULLET_COOLDOWN_SECONDS = 0.45
CHALLENGE_DURATION_SECONDS = 3.0
RESPAWN_DELAY_SECONDS = 3.0
PICKUP_INTERVAL_SECONDS = 4.0
MAX_PICKUPS = 8
VISIBILITY_RADIUS = 8

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

PICKUP_TYPES = (
    ("health", "+"),
    ("shield", "O"),
    ("rapid", "="),
)


class Game:
    """Maintain players, combat, challenges, pickups and world snapshots."""

    def __init__(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        seed: int | None = None,
    ) -> None:
        self.lock = threading.RLock()
        
        #self.maze = Maze(width, height, seed)
        self.maze = Maze()

        self.players: dict[int, Player] = {}
        self.projectiles: dict[int, Projectile] = {}
        self.challenges: dict[int, Challenge] = {}
        self.challenge_queues: dict[int, deque[int]] = {}
        self.pickups: dict[int, Pickup] = {}
        self.logs: deque[str] = deque(maxlen=MAX_LOG_ENTRIES)

        self.next_player_id = 1
        self.next_object_id = 1
        self.random = random.Random(seed)

        now = time.monotonic()
        self.last_tick = now
        self.pickup_clock = now

    def _new_object_id(self) -> int:
        """Return the next unique ID used by projectiles and world objects."""
        object_id = self.next_object_id
        self.next_object_id += 1
        return object_id

    def log(self, text: str) -> None:
        """Add the newest game event to the start of the bounded log."""
        self.logs.appendleft(text)

    def add_player(self, name: str) -> int:
        """Create a player on an unoccupied floor cell and return its ID."""
        with self.lock:
            player_id = self.next_player_id
            self.next_player_id += 1

            occupied = ((player.x, player.y) for player in self.players.values())
            x, y = self.maze.random_floor(occupied)
            safe_name = name[:MAX_PLAYER_NAME_LENGTH] or f"Player{player_id}"

            player = Player(player_id, safe_name, x, y)
            self.players[player_id] = player
            self.challenge_queues[player_id] = deque()
            self.log(f"{player.name} entered the maze")
            return player_id

    def remove_player(self, player_id: int) -> None:
        """Remove a player and every projectile currently owned by that player."""
        with self.lock:
            player = self.players.pop(player_id, None)
            self.challenge_queues.pop(player_id, None)

            if player:
                self.log(f"{player.name} left the maze")

            owned_projectile_ids = [
                object_id
                for object_id, projectile in self.projectiles.items()
                if projectile.owner_id == player_id
            ]
            for object_id in owned_projectile_ids:
                self.projectiles.pop(object_id, None)

    def handle(self, player_id: int, message: dict[str, Any]) -> None:
        """Validate and dispatch one action received from a connected client."""
        action = message.get("action")

        with self.lock:
            if player_id not in self.players:
                return

            if action == "move":
                self.move(player_id, str(message.get("direction", "")))
            elif action == "shoot":
                self.shoot(player_id)
            elif action == "cast":
                self.cast(player_id, str(message.get("spell", "")))
            elif action == "counter":
                try:
                    challenge_id = int(message.get("challenge_id", 0))
                except (TypeError, ValueError):
                    return
                self.counter(
                    player_id,
                    challenge_id,
                    str(message.get("word", "")),
                )

    def move(self, player_id: int, direction: str) -> None:
        """Move a living, unfrozen player to an unoccupied floor cell."""
        player = self.players[player_id]
        now = time.monotonic()

        if (
            not player.alive
            or now < player.frozen_until
            or direction not in DIRECTIONS
        ):
            return

        delta_x, delta_y = DIRECTIONS[direction]
        player.direction = direction
        next_x = player.x + delta_x
        next_y = player.y + delta_y

        blocked_by_player = any(
            other.alive
            and other.id != player_id
            and (other.x, other.y) == (next_x, next_y)
            for other in self.players.values()
        )

        if self.maze.walkable(next_x, next_y) and not blocked_by_player:
            player.x = next_x
            player.y = next_y
            self.collect(player)

    def shoot(self, player_id: int) -> None:
        """Fire a bullet in the player's current direction when off cooldown."""
        player = self.players[player_id]
        now = time.monotonic()

        if not player.alive or now < player.cooldowns.get("bullet", 0):
            return

        player.cooldowns["bullet"] = now + BULLET_COOLDOWN_SECONDS
        delta_x, delta_y = DIRECTIONS[player.direction]
        glyph = "-" if delta_x else "|"
        self._spawn_projectile(
            player,
            kind="bullet",
            glyph=glyph,
            damage=14,
            reach=10,
            delta_x=delta_x,
            delta_y=delta_y,
        )

    def cast(self, player_id: int, spell_key: str) -> None:
        """Cast a spell, applying a combo when the recent sequence matches."""
        player = self.players[player_id]
        now = time.monotonic()

        if (
            not player.alive
            or spell_key not in SPELLS
            or now < player.cooldowns.get(spell_key, 0)
        ):
            return

        player.recent_spells = [
            recent
            for recent in player.recent_spells
            if now - recent[1] <= COMBO_WINDOW
        ]

        selected_spell_key = spell_key
        if player.recent_spells:
            previous_spell_key = player.recent_spells[-1][0]
            selected_spell_key = COMBOS.get(
                (previous_spell_key, spell_key),
                spell_key,
            )

        spell = SPELLS[selected_spell_key]
        player.cooldowns[spell_key] = now + SPELLS[spell_key].cooldown
        player.recent_spells.append((spell_key, now))

        if spell.support:
            self._apply_support_spell(player, spell.effect)
            return

        delta_x, delta_y = DIRECTIONS[player.direction]
        self._spawn_projectile(
            player,
            kind=selected_spell_key,
            glyph=spell.glyph,
            damage=spell.damage,
            reach=spell.range,
            delta_x=delta_x,
            delta_y=delta_y,
        )
        self.log(f"{player.name} cast {spell.name}")

    def _apply_support_spell(self, player: Player, effect: str) -> None:
        """Apply a non-projectile support spell to its caster."""
        if effect == "shield":
            player.shield = min(60, player.shield + 30)
            self.log(f"{player.name} raised a shield")
        elif effect == "heal":
            player.hp = min(player.max_hp, player.hp + 30)
            self.log(f"{player.name} healed")
        elif effect == "fortress":
            player.shield = min(100, player.shield + 60)
            player.hp = min(player.max_hp, player.hp + 45)
            self.log(f"{player.name} cast Fortress #")

    def _spawn_projectile(
        self,
        player: Player,
        kind: str,
        glyph: str,
        damage: int,
        reach: int,
        delta_x: int,
        delta_y: int,
    ) -> None:
        """Create a projectile in the adjacent cell when that cell is walkable."""
        next_x = player.x + delta_x
        next_y = player.y + delta_y

        if not self.maze.walkable(next_x, next_y):
            return

        object_id = self._new_object_id()
        self.projectiles[object_id] = Projectile(
            object_id,
            player.id,
            next_x,
            next_y,
            delta_x,
            delta_y,
            kind,
            glyph,
            damage,
            reach,
        )

    def counter(self, player_id: int, challenge_id: int, word: str) -> None:
        """Resolve the active challenge when the submitted counter is correct."""
        challenge_queue = self.challenge_queues.get(player_id)
        if not challenge_queue or challenge_queue[0] != challenge_id:
            return

        challenge = self.challenges.get(challenge_id)
        if not challenge:
            challenge_queue.popleft()
            return

        correct_word = word.strip().lower() == challenge.counter
        submitted_in_time = time.monotonic() <= challenge.expires_at

        if submitted_in_time and correct_word:
            challenge_queue.popleft()
            self.challenges.pop(challenge_id, None)
            player = self.players.get(player_id)
            if player:
                self.log(f"{player.name} countered {challenge.spell}")

    def _create_challenge(
        self,
        projectile: Projectile,
        target: Player,
    ) -> None:
        """Queue a timed word challenge for a player hit by a spell."""
        spell = SPELLS[projectile.kind]
        challenge_id = self._new_object_id()
        now = time.monotonic()
        challenge_queue = self.challenge_queues[target.id]

        start = now
        if challenge_queue:
            last_challenge = self.challenges.get(challenge_queue[-1])
            if last_challenge:
                start = max(now, last_challenge.expires_at)

        challenge = Challenge(
            challenge_id,
            projectile.owner_id,
            target.id,
            spell.name,
            spell.counter,
            spell.damage,
            start + CHALLENGE_DURATION_SECONDS,
            spell.effect,
        )
        self.challenges[challenge_id] = challenge
        challenge_queue.append(challenge_id)

        caster = self.players.get(projectile.owner_id)
        caster_name = caster.name if caster else "Unknown"
        self.log(f"{caster_name} threatens {target.name} with {spell.name}")

    def damage(
        self,
        target: Player,
        amount: int,
        attacker_id: int,
        effect: str = "",
    ) -> None:
        """Apply shields, damage, status effects, defeat and scoring."""
        if target.shield:
            absorbed = min(target.shield, amount)
            target.shield -= absorbed
            amount -= absorbed

        target.hp = max(0, target.hp - amount)
        now = time.monotonic()

        if effect in ("poison", "plague"):
            duration = 8 if effect == "plague" else 5
            target.poison_until = now + duration
            target.poison_next_tick = now + 1

        if effect in ("freeze", "storm"):
            duration = 3 if effect == "storm" else 2
            target.frozen_until = max(target.frozen_until, now + duration)

        if target.hp <= 0 and target.respawn_at <= 0:
            target.deaths += 1
            target.respawn_at = now + RESPAWN_DELAY_SECONDS
            attacker = self.players.get(attacker_id)

            if attacker and attacker.id != target.id:
                attacker.score += 1

            attacker_name = attacker.name if attacker else "the maze"
            self.log(f"{target.name} was defeated by {attacker_name}")

    def collect(self, player: Player) -> None:
        """Apply and remove every pickup occupying the player's cell."""
        for object_id, item in list(self.pickups.items()):
            if (item.x, item.y) != (player.x, player.y):
                continue

            if item.kind == "health":
                player.hp = min(player.max_hp, player.hp + 35)
            elif item.kind == "shield":
                player.shield = min(100, player.shield + 35)
            elif item.kind == "rapid":
                rapid_limit = time.monotonic() + 0.5
                player.cooldowns = {
                    key: min(expiry, rapid_limit)
                    for key, expiry in player.cooldowns.items()
                }

            self.log(f"{player.name} picked up {item.kind}")
            self.pickups.pop(object_id, None)

    def tick(self) -> None:
        """Advance projectiles, challenges, effects, respawns and pickups."""
        with self.lock:
            now = time.monotonic()
            self._tick_projectiles()
            self._tick_challenges(now)
            self._tick_players(now)
            self._spawn_pickup_if_due(now)
            self.last_tick = now

    def _tick_projectiles(self) -> None:
        """Move projectiles one cell and resolve collisions."""
        for object_id, projectile in list(self.projectiles.items()):
            target = next(
                (
                    player
                    for player in self.players.values()
                    if player.id != projectile.owner_id
                    and player.alive
                    and (player.x, player.y) == (projectile.x, projectile.y)
                ),
                None,
            )

            if target:
                if projectile.kind == "bullet":
                    self.damage(target, projectile.damage, projectile.owner_id)
                else:
                    self._create_challenge(projectile, target)
                self.projectiles.pop(object_id, None)
                continue

            next_x = projectile.x + projectile.dx
            next_y = projectile.y + projectile.dy
            projectile.remaining -= 1

            if (
                projectile.remaining <= 0
                or not self.maze.walkable(next_x, next_y)
            ):
                self.projectiles.pop(object_id, None)
            else:
                projectile.x = next_x
                projectile.y = next_y

    def _tick_challenges(self, now: float) -> None:
        """Apply each player's expired queue-head challenge."""
        for player_id, challenge_queue in self.challenge_queues.items():
            while challenge_queue:
                challenge = self.challenges.get(challenge_queue[0])
                if not challenge:
                    challenge_queue.popleft()
                    continue

                if now < challenge.expires_at:
                    break

                challenge_queue.popleft()
                self.challenges.pop(challenge.id, None)
                target = self.players.get(player_id)

                if target and target.alive:
                    self.damage(
                        target,
                        challenge.damage,
                        challenge.caster_id,
                        challenge.effect,
                    )
                break

    def _tick_players(self, now: float) -> None:
        """Apply poison ticks and respawn defeated players."""
        for player in self.players.values():
            if (
                player.poison_until > now
                and player.poison_next_tick <= now
                and player.alive
            ):
                player.poison_next_tick = now + 1
                self.damage(player, 4, 0)

            if player.respawn_at and now >= player.respawn_at:
                occupied = (
                    (other.x, other.y)
                    for other in self.players.values()
                    if other.id != player.id and other.alive
                )
                player.x, player.y = self.maze.random_floor(occupied)
                player.hp = player.max_hp
                player.shield = 0
                player.respawn_at = 0
                player.poison_until = 0
                player.frozen_until = 0
                self.log(f"{player.name} respawned")

    def _spawn_pickup_if_due(self, now: float) -> None:
        """Spawn a random pickup when the interval and capacity permit."""
        if (
            now - self.pickup_clock < PICKUP_INTERVAL_SECONDS
            or len(self.pickups) >= MAX_PICKUPS
        ):
            return

        occupied = [
            (player.x, player.y) for player in self.players.values()
        ] + [
            (pickup.x, pickup.y) for pickup in self.pickups.values()
        ]
        x, y = self.maze.random_floor(occupied)
        kind, glyph = self.random.choice(PICKUP_TYPES)
        object_id = self._new_object_id()
        self.pickups[object_id] = Pickup(object_id, x, y, kind, glyph)
        self.pickup_clock = now

    def snapshot(self, player_id: int) -> dict[str, Any]:
        """Build the fog-of-war state payload for one connected player."""
        with self.lock:
            now = time.monotonic()
            current_player = self.players.get(player_id)
            if not current_player:
                return {"type": "closed"}

            visible = visible_cells(
                self.maze,
                current_player.x,
                current_player.y,
                VISIBILITY_RADIUS,
            )
            challenge_queue = self.challenge_queues.get(player_id, deque())
            public_challenges = self._public_challenges(challenge_queue, now)

            return {
                "type": "state",
                "you": player_id,
                "maze": self.maze.rows(),
                "visible": [[x, y] for x, y in visible],
                "players": [
                    player.public()
                    for player in self.players.values()
                    if (player.x, player.y) in visible or player.id == player_id
                ],
                "projectiles": [
                    projectile.public()
                    for projectile in self.projectiles.values()
                    if (projectile.x, projectile.y) in visible
                ],
                "pickups": [
                    pickup.public()
                    for pickup in self.pickups.values()
                    if (pickup.x, pickup.y) in visible
                ],
                "challenges": public_challenges,
                "logs": list(self.logs),
                "scoreboard": self._scoreboard(),
            }

    def _public_challenges(
        self,
        challenge_queue: Iterable[int],
        now: float,
    ) -> list[dict[str, Any]]:
        """Serialise a player's queued challenges for the client UI."""
        output: list[dict[str, Any]] = []

        for index, challenge_id in enumerate(challenge_queue):
            challenge = self.challenges.get(challenge_id)
            if not challenge:
                continue

            data = challenge.public(now)
            data["active"] = index == 0
            caster = self.players.get(challenge.caster_id)
            data["caster_name"] = caster.name if caster else "Unknown"
            output.append(data)

        return output

    def _scoreboard(self) -> list[dict[str, Any]]:
        """Return players ordered by score descending, then deaths ascending."""
        players = [player.public() for player in self.players.values()]
        return sorted(
            players,
            key=lambda player: (-player["score"], player["deaths"]),
        )
