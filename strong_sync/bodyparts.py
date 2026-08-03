from __future__ import annotations

BODY_PARTS = ["shoulders", "chest", "legs", "arms", "back", "core", "other"]

# Normalized muscle-group token -> output file stem.
GROUP_MAP: dict[str, str] = {
    # shoulders
    "shoulders": "shoulders", "shoulder": "shoulders", "delts": "shoulders",
    "deltoids": "shoulders", "front delts": "shoulders", "rear delts": "shoulders",
    "side delts": "shoulders", "lateral delts": "shoulders", "traps": "shoulders",
    "trapezius": "shoulders", "rotator cuff": "shoulders",
    # chest
    "chest": "chest", "pecs": "chest", "pectorals": "chest",
    "upper chest": "chest", "lower chest": "chest",
    # back
    "back": "back", "lats": "back", "latissimus dorsi": "back",
    "upper back": "back", "lower back": "back", "middle back": "back",
    "rhomboids": "back", "spinal erectors": "back", "erector spinae": "back",
    # arms
    "arms": "arms", "biceps": "arms", "triceps": "arms", "forearms": "arms",
    "brachialis": "arms", "upper arms": "arms",
    # legs
    "legs": "legs", "quadriceps": "legs", "quads": "legs", "hamstrings": "legs",
    "glutes": "legs", "calves": "legs", "adductors": "legs", "abductors": "legs",
    "hip flexors": "legs", "thighs": "legs", "upper legs": "legs", "lower legs": "legs",
    # core
    "core": "core", "abs": "core", "abdominals": "core", "obliques": "core",
    "transverse abdominis": "core", "waist": "core",
    # Strong tag slugs (from _links.tag hrefs)
    "upper legs": "legs", "lower legs": "legs", "upper arms": "arms",
    "full body": "other", "weightlifting": "other", "machine": "other",
    # explicitly parked in other
    "cardio": "other", "full body": "other", "olympic": "other",
    "olympic weightlifting": "other", "other": "other", "none": "other",
}

# Substring hints applied to the EXERCISE NAME when the muscle group is
# missing or unrecognized. Ordered: first match wins, so put specific terms
# ahead of general ones.
NAME_HINTS: list[tuple[str, str]] = [
    ("lateral raise", "shoulders"), ("front raise", "shoulders"),
    ("overhead press", "shoulders"), ("shoulder press", "shoulders"),
    ("military press", "shoulders"), ("arnold press", "shoulders"),
    ("face pull", "shoulders"), ("shrug", "shoulders"), ("upright row", "shoulders"),
    ("rear delt", "shoulders"),

    ("bench press", "chest"), ("chest press", "chest"), ("chest fly", "chest"),
    ("pec deck", "chest"), ("dip", "chest"), ("push up", "chest"),
    ("push-up", "chest"), ("pushup", "chest"), ("cable crossover", "chest"),
    ("fly", "chest"),

    ("pull up", "back"), ("pull-up", "back"), ("pullup", "back"),
    ("chin up", "back"), ("chin-up", "back"), ("lat pulldown", "back"),
    ("pulldown", "back"), ("row", "back"), ("deadlift", "back"),
    ("pullover", "back"), ("back extension", "back"), ("good morning", "back"),

    ("jm press", "arms"), ("tate press", "arms"), ("jm ", "arms"),
    ("bicep", "arms"), ("curl", "arms"), ("tricep", "arms"),
    ("skull crusher", "arms"), ("pushdown", "arms"), ("press down", "arms"),
    ("kickback", "arms"), ("wrist", "arms"), ("hammer", "arms"), ("JM", "arms"),

    ("squat", "legs"), ("leg press", "legs"), ("leg extension", "legs"),
    ("leg curl", "legs"), ("lunge", "legs"), ("calf", "legs"),
    ("hip thrust", "legs"), ("glute", "legs"), ("step up", "legs"),
    ("bulgarian", "legs"), ("hack", "legs"), ("adductor", "legs"),
    ("abductor", "legs"),

    ("crunch", "core"), ("sit up", "core"), ("sit-up", "core"),
    ("plank", "core"), ("ab ", "core"), ("oblique", "core"),
    ("leg raise", "core"), ("knee raise", "core"), ("russian twist", "core"),
    ("hollow", "core"), ("woodchop", "core"),
]


def first_recognized_tag(slugs: list[str]) -> str | None:
    """Pick the first tag that maps to a body part.

    Strong tags an exercise with a slug like `back` or `upper_legs`. A single
    exercise can carry several tags (muscle plus equipment), so scan for one we
    recognize rather than blindly taking tags[0].
    """
    for slug in slugs:
        if _normalize(slug) in GROUP_MAP:
            return slug
    return None


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


def classify(exercise_name: str, body_part_raw: str | None) -> str:
    """Return one of BODY_PARTS for a given exercise."""
    if body_part_raw:
        token = _normalize(body_part_raw)
        if token in GROUP_MAP:
            return GROUP_MAP[token]
        for key, bucket in GROUP_MAP.items():
            if key in token:
                return bucket

    name = _normalize(exercise_name or "")
    for hint, bucket in NAME_HINTS:
        if hint in name:
            return bucket
    return "other"
