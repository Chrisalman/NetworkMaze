from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Spell:
    name: str
    glyph: str
    damage: int
    range: int
    cooldown: float
    counter: str
    effect: str = ""
    support: bool = False


SPELLS = {
    "rock": Spell("Rock", "R", 18, 6, 2.0, "paper"),
    "paper": Spell("Paper", "P", 15, 7, 2.0, "scissors"),
    "scissors": Spell("Scissors", "S", 21, 5, 2.0, "rock"),
    "fireball": Spell("Fireball", "*", 28, 8, 4.0, "douse", "burn"),
    "poison": Spell("Poison", "%", 8, 7, 5.0, "cleanse", "poison"),
    "freeze": Spell("Freeze", "+", 8, 6, 5.0, "thaw", "freeze"),
    "lightning": Spell("Lightning", "!", 24, 9, 4.0, "ground", "shock"),
    "shield": Spell("Shield", "]", 0, 0, 7.0, "", "shield", True),
    "heal": Spell("Heal", "~", 0, 0, 7.0, "", "heal", True),
    "plague_fire": Spell("Plague Fire", "&", 38, 9, 8.0, "douse", "plague"),
    "storm": Spell("Storm", "@", 34, 10, 8.0, "ground", "storm"),
    "fortress": Spell("Fortress", "#", 0, 0, 10.0, "", "fortress", True),
}

COMBOS = {
    ("poison", "fireball"): "plague_fire",
    ("freeze", "lightning"): "storm",
    ("shield", "heal"): "fortress",
}
COMBO_WINDOW = 5.0
