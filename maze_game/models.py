from __future__ import annotations
from dataclasses import dataclass, field
import time


@dataclass
class Player:
    id: int
    name: str
    x: int
    y: int
    direction: str = "right"
    hp: int = 100
    max_hp: int = 100
    score: int = 0
    deaths: int = 0
    shield: int = 0
    frozen_until: float = 0.0
    poison_until: float = 0.0
    poison_next_tick: float = 0.0
    respawn_at: float = 0.0
    cooldowns: dict[str, float] = field(default_factory=dict)
    recent_spells: list[tuple[str, float]] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.hp > 0 and self.respawn_at <= 0

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "x": self.x, "y": self.y,
                "direction": self.direction, "hp": self.hp, "max_hp": self.max_hp,
                "score": self.score, "deaths": self.deaths, "shield": self.shield,
                "alive": self.alive}


@dataclass
class Projectile:
    id: int
    owner_id: int
    x: int
    y: int
    dx: int
    dy: int
    kind: str
    glyph: str
    damage: int
    remaining: int
    created: float = field(default_factory=time.monotonic)

    def public(self) -> dict:
        return {"id": self.id, "owner_id": self.owner_id, "x": self.x, "y": self.y,
                "kind": self.kind, "glyph": self.glyph}


@dataclass
class Challenge:
    id: int
    caster_id: int
    defender_id: int
    spell: str
    counter: str
    damage: int
    expires_at: float
    effect: str = ""

    def public(self, now: float) -> dict:
        return {"id": self.id, "caster_id": self.caster_id, "spell": self.spell,
                "counter_length": len(self.counter), "remaining": max(0.0, self.expires_at-now)}


@dataclass
class Pickup:
    id: int
    x: int
    y: int
    kind: str
    glyph: str

    def public(self) -> dict:
        return {"id": self.id, "x": self.x, "y": self.y, "kind": self.kind, "glyph": self.glyph}
