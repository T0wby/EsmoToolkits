"""Offline checks for parse_esmo's pure helpers. No emulator, no adb."""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import parse_esmo as pe  # noqa: E402


def test_ability_roundtrip_against_sample():
    """Every ability in the sample must re-parse from its own `raw` string."""
    data = json.loads((ROOT / "examples" / "champions.sample.json").read_text(encoding="utf-8"))
    seen = 0
    for champ in data["champions"]:
        for ability in champ["abilities"]:
            assert pe.parse_ability(ability["raw"]) == ability, f"{champ['name']} {ability['slot']}"
            seen += 1
    assert seen > 0, "sample has no abilities to check"


def test_ability_needs_three_lines():
    assert pe.parse_ability("Q\nLine skillshot") is None


def test_matchup_row():
    assert pe.parse_matchup("Automata\nvs\nGambler\n67.6%\nWR\n34\nG") == {
        "opponent": "Gambler", "win_rate": 67.6, "games": 34,
    }


def test_matchup_rejects_placeholder_opponent():
    """The cached grid emits 'X vs 1 52.0%' before real data loads."""
    assert pe.parse_matchup("Automata\nvs\n1\n52.0%") is None


def test_is_chrome():
    assert pe.is_chrome("Overview\nTab 1 of 3")
    assert pe.is_chrome("Back")
    assert not pe.is_chrome("Automata")


def test_num():
    assert pe.num("67.6%") == 67.6
    assert pe.num("34W 12L", int) == 34
    assert pe.num("") is None
