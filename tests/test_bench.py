"""installer/bench.py: GPU benchmark and profile.

    python tests/test_bench.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run


def test_bench_presets_are_coherent() -> None:
    """Every preset must be runnable and its result storable."""
    from installer import bench

    names = [p.name for p in bench.PRESETS]
    check(len(names) == len(set(names)), f"duplicate preset names: {names}")
    check("software" in names, "the software baseline is missing")

    for preset in bench.PRESETS:
        check(bench.preset_by_name(preset.name) is preset, f"{preset.name} not findable")
        exports = bench.client_exports(preset)
        check(exports, f"{preset.name} exports nothing")
        for line in exports.splitlines():
            check(line.startswith("export "), f"{preset.name}: bad export line {line!r}")

    check(
        bench.preset_by_name("nonexistent") is None,
        "an unknown preset name resolved to something",
    )


def test_bench_set_profile_manually_clears_score() -> None:
    """A Settings override (set_profile_manually) has no measured score —
    leaving a stale one from an earlier benchmark would misreport the
    override as something Bench actually measured."""
    from installer import bench, config

    original_profile = config.get(bench.PROFILE_KEY)
    original_score = config.get(bench.SCORE_KEY)
    try:
        check(bench.save_profile(bench.PRESETS[0], 42), "could not save a measured profile")
        check(config.get(bench.SCORE_KEY) == "42", "score did not round-trip")

        check(bench.set_profile_manually(bench.PRESETS[1]), "manual override reported failure")
        check(bench.load_profile() is bench.PRESETS[1], "manual override did not stick")
        check(config.get(bench.SCORE_KEY) is None, "a stale score survived a manual override")
    finally:
        for key, value in (
            (bench.PROFILE_KEY, original_profile),
            (bench.SCORE_KEY, original_score),
        ):
            if value is None:
                config.unset(key)
            else:
                config.set_value(key, value)


TESTS = [
    test_bench_presets_are_coherent,
    test_bench_set_profile_manually_clears_score,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
