"""Australian Business Number checksum and generation.

Generated ABNs have a valid checksum but are random, so one could coincide with a real
registered ABN. They are only ever paired with fictional business names.
"""

import random

_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def is_valid_abn(abn: str) -> bool:
    compact = abn.replace(" ", "")
    if len(compact) != 11 or not compact.isdigit() or compact[0] == "0":
        return False
    digits = [int(c) for c in compact]
    digits[0] -= 1
    return sum(w * d for w, d in zip(_WEIGHTS, digits, strict=True)) % 89 == 0


def generate_abn(rng: random.Random) -> str:
    tail = "".join(str(rng.randint(0, 9)) for _ in range(9))
    # Prefixes 10..99 contribute 0..89 to the weighted sum, so every residue mod 89 is reachable.
    for prefix in range(10, 100):
        candidate = f"{prefix}{tail}"
        if is_valid_abn(candidate):
            return candidate
    raise AssertionError("unreachable: some prefix always satisfies the checksum")


def format_abn(abn: str) -> str:
    return f"{abn[0:2]} {abn[2:5]} {abn[5:8]} {abn[8:11]}"
