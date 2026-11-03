"""Class groups shared by the parser (core/ai_handler.py) and the frame buffer.

Kept in one place so the two cannot drift apart.
"""


def normalize_class(name: str) -> str:
    """Lowercase a detector class name and use underscores, so 'Cave Spider',
    'cave-spider' and 'cave_spider' all become 'cave_spider'."""
    return "_".join(str(name).lower().replace("-", " ").split())


THREAT_CLASSES = {
    "creeper", "zombie", "skeleton", "enderman", "witch",
    "spider", "cave_spider", "ghast", "blaze", "wither",
    "drowned", "husk", "stray", "zombie_villager", "creaking"
}

RESOURCE_CLASSES = {
    "diamond_ore", "gold_ore", "iron_ore", "copper_ore",
    "coal_ore", "lapis_ore", "emerald_ore", "ancient_debris",
    "diamond", "gold", "iron", "copper", "coal", "lapis",
    "emerald", "netherite", "redstone", "redstone_ore"
}

STATUS_CLASSES = {
    "health_bar", "hunger_bar", "exp_bar", "armor",
    "shield", "effect"
}

HAZARD_CLASSES = {
    "water", "lava", "fire", "cactus", "void",
    "suffocation", "damage"
}

PASSIVE_CLASSES = {
    "cow", "pig", "sheep", "horse", "donkey",
    "duck", "fish", "chicken", "villager",
}

# Things that can hurt you when they get close. Water, suffocation and damage are
# hazards for the parser's wording, but they are not something to "approach".
DANGEROUS_CLASSES = THREAT_CLASSES | {"lava", "fire", "cactus", "void"}
