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


# ------------------------------------------------------------------ overview
# tests/fixtures/overview_automata.xml is a uiautomator dump in the shape the game
# produces: text in content-desc, newlines as &#10;, one node per card. The strings
# are Automata's, taken verbatim from examples/champions.sample.json.
FIXTURE = ROOT / "tests" / "fixtures" / "overview_automata.xml"


def sample_champion(name="Automata"):
    data = json.loads((ROOT / "examples" / "champions.sample.json").read_text(encoding="utf-8"))
    return next(c for c in data["champions"] if c["name"] == name)


def test_overview_matches_the_captured_champion():
    got = pe.parse_overview([FIXTURE])
    want = sample_champion()
    for field in ("class", "class_description", "attack_type", "damage_type",
                  "base_health", "base_resource", "resource_type"):
        assert got[field] == want[field], field
    assert got["abilities"] == want["abilities"]


def test_overview_skips_chrome_and_unusable_nodes():
    """Back/Playbook/tab labels, empty content-desc and missing bounds contribute nothing."""
    descs = [n[0] for n in pe.nodes_of(FIXTURE)]
    assert "Back" in descs and "" not in descs
    assert descs.count("Ranged") == 1  # the bounds-less duplicate node is dropped
    assert pe.parse_overview([FIXTURE])["class"] == "Battle Mage"


def write_dump(path, *descs):
    """Minimal dump with one node per string, top to bottom."""
    def esc(s):
        return s.replace("&", "&amp;").replace('"', "&quot;").replace("\n", "&#10;")
    nodes = "".join(
        f'<node class="android.view.View" content-desc="{esc(d)}" '
        f'bounds="[100,{i * 100}][900,{i * 100 + 90}]" />'
        for i, d in enumerate(descs)
    )
    path.write_text(f'<hierarchy rotation="0">{nodes}</hierarchy>', encoding="utf-8")
    return path


def test_overview_sorts_abilities_and_keeps_the_first_of_a_repeated_slot(tmp_path):
    """Scrolling re-emits nodes, so the same slot can appear twice across dumps."""
    dump = write_dump(tmp_path / "ui.xml",
                      "R\nPoint-targeted\n200 (+100/lvl)\n110s\n9.0",
                      "Q\nLine skillshot\n60 (+35/lvl)\n6s\n9.0",
                      "Q\nLine skillshot\nstale duplicate\n6s\n9.0")
    abilities = pe.parse_overview([dump])["abilities"]
    assert [a["slot"] for a in abilities] == ["Q", "R"]
    assert abilities[0]["scaling"] == "60 (+35/lvl)"


def test_overview_keeps_only_the_first_class_of_a_concatenated_card(tmp_path):
    """A transient render can merge two class cards into one node."""
    dump = write_dump(tmp_path / "ui.xml",
                      "Diver\nDivers charge the backline and blow up a single target.\n"
                      "Battle Mage\nShort-range mages who wade into combat.")
    got = pe.parse_overview([dump])
    assert got["class"] == "Diver"
    assert got["class_description"] == "Divers charge the backline and blow up a single target."


def test_overview_of_an_unreadable_dump_is_empty(tmp_path):
    """A truncated dump must yield nothing rather than raise."""
    broken = tmp_path / "broken.xml"
    broken.write_text("<hierarchy><node content-desc=", encoding="utf-8")
    assert pe.nodes_of(broken) == []
    assert pe.parse_overview([broken])["abilities"] == []
