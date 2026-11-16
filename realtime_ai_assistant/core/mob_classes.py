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
    "drowned", "husk", "stray", "zombie_villager", "creaking",
    # every other mob that can attack the player (a class missing here is silently ignored by the parser)
    "phantom", "zombie_piglin", "zombified_piglin", "zombie_pigman", "piglin_brute", "hoglin", "zoglin",
    "pillager", "vindicator", "evoker", "ravager", "vex", "illusioner",
    "slime", "magma_cube", "silverfish", "endermite", "guardian", "elder_guardian",
    "shulker", "warden", "bogged", "breeze", "wither_skeleton",
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

# Ore tiers decide how much Maria cares by default. Base names: "deepslate_diamond_ore"
# and "Diamond" both become "diamond" (see ore_base).
HIGHEST_ORES = {"diamond", "ancient_debris", "netherite"}
RARE_ORES = {"gold", "lapis", "emerald", "redstone"}
COMMON_ORES = {"iron", "copper", "coal"}
ALL_ORES = HIGHEST_ORES | RARE_ORES | COMMON_ORES

# "animals" for the player's watch requests (a villager is not an animal)
ANIMAL_CLASSES = PASSIVE_CLASSES - {"villager"}
FOOD_ANIMALS = {"cow", "pig", "sheep", "chicken"}


def ore_base(name: str) -> str:
    """'Deepslate Diamond Ore' -> 'diamond'. Returns '' when it is not an ore."""
    base = normalize_class(name)
    if base.startswith("deepslate_"):
        base = base[len("deepslate_"):]
    if base.endswith("_ore"):
        base = base[:-len("_ore")]
    return base if base in ALL_ORES else ""


def ore_tier(name: str) -> str:
    """'highest', 'rare', 'common', or '' when it is not an ore."""
    base = ore_base(name)
    if base in HIGHEST_ORES:
        return "highest"
    if base in RARE_ORES:
        return "rare"
    if base in COMMON_ORES:
        return "common"
    return ""


# Things that can hurt you when they get close. Water, suffocation and damage are
# hazards for the parser's wording, but they are not something to "approach".
DANGEROUS_CLASSES = THREAT_CLASSES | {"lava", "fire", "cactus", "void"}
