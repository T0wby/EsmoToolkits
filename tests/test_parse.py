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


# ------------------------------------------------- positions with no games
# A position the champion plays but has no games in during the capture window
# renders "Not enough data": no win block, so no meta rows and (before this) no
# position either. The position is recovered from the saved selector strip,
# cross-checked against the icon count under the champion name.
def write_header(path, icons, *descs):
    """Dump with `icons` position icons under the name, then normal nodes."""
    head = "".join(
        f'<node class="android.widget.ImageView" content-desc="" '
        f'bounds="[{959 + i * 30},198][{986 + i * 30},225]" />'
        for i in range(icons))
    write_dump(path, *descs)
    path.write_text(path.read_text(encoding="utf-8").replace(
        '<hierarchy rotation="0">', f'<hierarchy rotation="0">{head}'), encoding="utf-8")
    return path


def write_strip(path, enabled):
    """The selector crop: playable icons peak at 227, greyed-out ones at 125."""
    from PIL import Image
    im = Image.new("L", (540, 72), 0)
    for i, on in enumerate(enabled):
        im.paste(227 if on else 125, (i * 108 + 20, 20, i * 108 + 80, 52))
    im.save(path)
    return path


WINBLOCK = "20.0%\n2W 8L · 10 games\nKDA\n1.27"


def build_capture(tmp_path, name, icons, enabled, meta_roles=(), meta_attempted=True):
    raw = tmp_path / "raw" / name
    raw.mkdir(parents=True)
    (tmp_path / "portraits").mkdir(exist_ok=True)
    write_header(raw / "overview_00.xml", icons, "Ranged", "Magic")
    files = {"overview": ["overview_00.xml"]}
    for i, role in enumerate(meta_roles):
        # Distinct samples per role - two identical ones mean the tap was ignored,
        # and the parser drops the duplicate as a position that is not played.
        write_dump(raw / f"meta_{role}_00.xml", "Aug 26 - Aug 27",
                   "Pick rate\n0.6%\nBan rate\n2.6%",
                   WINBLOCK.replace("2W 8L · 10", f"{2 + i}W 8L · {10 + i}"))
        files[f"meta_{role}"] = [f"meta_{role}_00.xml"]
    write_strip(tmp_path / "portraits" / f"_rolestrip_{name}.png", enabled)
    (tmp_path / "captured.json").write_text(json.dumps({"champions": [{
        "name": name, "dir": name, "files": files, "roles": list(meta_roles),
        "incomplete": [] if meta_roles or not meta_attempted else ["meta"],
    }]}), encoding="utf-8")
    return tmp_path


def run_parser(capture_dir):
    import subprocess
    out = capture_dir / "champions.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "parse_esmo.py"),
                    "--dir", str(capture_dir), "--out", str(out)],
                   check=True, stdout=subprocess.DEVNULL)
    return json.loads(out.read_text(encoding="utf-8"))["champions"][0]


def test_position_without_games_is_kept_with_null_numbers(tmp_path):
    """Widow: one position, no games in the window, so no meta files at all."""
    champ = run_parser(build_capture(tmp_path, "Widow", 1,
                                     [False, True, False, False, False]))
    assert [p["position"] for p in champ["positions"]] == ["Jungle"]
    assert champ["positions"][0]["no_data"] is True
    assert champ["positions"][0]["win_rate"] is None
    assert champ["has_meta"] is False       # a known position is not a sample


def test_empty_position_joins_the_one_that_had_data(tmp_path):
    """Pixie: Support played, Top playable but empty."""
    champ = run_parser(build_capture(tmp_path, "Pixie", 2,
                                     [True, False, False, False, True],
                                     meta_roles=["Support"]))
    got = {p["position"]: p for p in champ["positions"]}
    assert sorted(got) == ["Support", "Top"]
    assert got["Top"]["no_data"] is True
    assert got["Support"].get("no_data") is None and got["Support"]["win_rate"] == 20.0
    assert champ["meta"]["win_rate"] == 20.0    # meta still comes from real data
    assert champ["has_meta"] is True


def test_an_unstyled_selector_frame_invents_nothing(tmp_path):
    """One bad frame paints every icon lit; the header icon count vetoes it."""
    champ = run_parser(build_capture(tmp_path, "Sentinel", 2, [True] * 5,
                                     meta_roles=["Top", "Support"]))
    assert sorted(p["position"] for p in champ["positions"]) == ["Support", "Top"]


def test_a_leftover_strip_is_ignored_when_meta_was_not_captured(tmp_path):
    """--no-meta never opens the Meta tab, so the strip on disk is last run's."""
    champ = run_parser(build_capture(tmp_path, "Widow", 1,
                                     [False, True, False, False, False],
                                     meta_attempted=False))
    assert champ["positions"] == []
