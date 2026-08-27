"""Checks for the preset launcher. Builds command lines only, runs nothing."""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import esmo  # noqa: E402


def test_preset_flags_come_first_then_passthrough():
    capture, _ = esmo.commands("weekly", ["--redo", "Brewer"], ".", "20260827")
    assert capture[2:] == ["--range", "7d", "--resume", "--scroll-method", "pagekeys",
                           "--redo", "Brewer"]


def test_a_flag_you_pass_overrides_the_preset():
    """Documented behaviour: preset first, yours last, argparse takes the last one."""
    capture, _ = esmo.commands("weekly", ["--range", "14d"], ".", "20260827")
    assert capture.index("--range", 3) > capture.index("--range")
    assert capture[-1] == "14d"
    assert esmo.resolve_range(capture) == "14d"


def test_parse_reads_the_folder_capture_writes_to():
    """Capture writes ./esmo_capture; the parse step must read that same folder."""
    _, parse = esmo.commands("weekly", [], ".", "20260827")
    assert parse[parse.index("--dir") + 1] == str(esmo.CAPTURE_DIR)
    assert esmo.CAPTURE_DIR == pathlib.Path.cwd() / "esmo_capture"


def test_workdir_moves_the_capture_folder_with_it(tmp_path):
    """The GUI runs with cwd set to the chosen folder, so --dir has to follow."""
    _, parse = esmo.commands("weekly", [], ".", "20260827", workdir=tmp_path)
    assert parse[parse.index("--dir") + 1] == str(tmp_path / "esmo_capture")


def test_output_is_datestamped_in_the_out_dir(tmp_path):
    _, parse = esmo.commands("daily", [], tmp_path, "20260827")
    assert parse[parse.index("--out") + 1] == str(tmp_path / "20260827_champions.json")


def test_every_preset_has_a_description():
    assert set(esmo.PRESETS) == set(esmo.DESCRIPTIONS)


# ---------------------------------------------------------------- filename pattern
def test_pattern_placeholders():
    assert esmo.format_name("{date}_{preset}_{range}.json", "20260827", "weekly", "7d") == \
        "20260827_weekly_7d.json"


def test_pattern_without_a_preset_says_custom():
    assert esmo.format_name("{preset}.json", "20260827", None, "7d") == "custom.json"


def test_unknown_placeholder_fails_before_the_capture_runs():
    try:
        esmo.format_name("{data}.json", "20260827", "weekly", "7d")
    except KeyError:
        return
    raise AssertionError("a typo'd placeholder must raise, not ship a broken filename")


def test_literal_out_wins_over_the_pattern(tmp_path):
    _, parse = esmo.commands("weekly", [], ".", "20260827", out=tmp_path / "patch-3.7.json")
    assert parse[parse.index("--out") + 1] == str(tmp_path / "patch-3.7.json")


def test_range_defaults_to_captures_own_default():
    assert esmo.resolve_range([]) == esmo.DEFAULT_RANGE
    assert esmo.resolve_range(["--range=24h"]) == "24h"


# ---------------------------------------------------------------- routing
def test_double_dash_sends_the_rest_to_the_parser():
    before, after = esmo.split_argv(["weekly", "--port", "5555", "--", "--strict-period"])
    assert before == ["weekly", "--port", "5555"]
    assert after == ["--strict-period"]


def test_parse_extra_lands_on_the_parse_command_only():
    capture, parse = esmo.commands("weekly", [], ".", "20260827",
                                   parse_extra=["--strict-period"])
    assert "--strict-period" not in capture
    assert parse[-1] == "--strict-period"


def test_split_argv_without_a_separator():
    assert esmo.split_argv(["weekly", "-n"]) == (["weekly", "-n"], [])


# ---------------------------------------------------------------- config
def test_config_presets_layer_over_the_builtins(tmp_path):
    cfg = {"presets": {"biweekly": {"capture": ["--range", "14d"], "parse": ["--strict-period"],
                                    "description": "mine"},
                       "weekly": ["--range", "9d"]}}
    presets = esmo.load_presets(cfg)
    assert presets["biweekly"]["parse"] == ["--strict-period"]
    assert presets["weekly"]["capture"] == ["--range", "9d"], "config wins on a name clash"
    assert presets["daily"]["capture"] == esmo.PRESETS["daily"], "built-ins survive"


def test_a_broken_config_is_not_fatal(tmp_path):
    bad = tmp_path / ".esmo.json"
    bad.write_text("{not json", encoding="utf-8")
    assert esmo.load_config(bad) == {}
    assert esmo.load_config(tmp_path / "absent.json") == {}


def test_config_roundtrip(tmp_path):
    path = tmp_path / ".esmo.json"
    esmo.save_config({"presets": {"x": ["--resume"]}}, path)
    assert json.loads(path.read_text(encoding="utf-8"))["presets"]["x"] == ["--resume"]


def test_config_preset_reaches_the_command_line():
    presets = esmo.load_presets({"presets": {"mine": {"capture": ["--range", "14d"],
                                                      "parse": ["--strict-period"]}}})
    capture, parse = esmo.commands("mine", [], ".", "20260827", presets)
    assert capture[2:] == ["--range", "14d"]
    assert parse[-1] == "--strict-period"


# ---------------------------------------------------------------- gui state mapping
def test_settings_roundtrip():
    """The GUI loads a preset by inverting this mapping, so it has to be exact."""
    s = {"range": "7d", "phase": "bottom", "scroll_method": "pagekeys", "limit": "3",
         "port": "5555", "resume": True, "no_meta": False, "no_roles": True,
         "extra": "--pull-apk", "merge": "old.json", "strict_period": True,
         "parse_extra": ""}
    cap, par = esmo.settings_to_args(s)
    back = esmo.args_to_settings(cap, par)
    assert back["range"] == "7d" and back["limit"] == "3" and back["port"] == "5555"
    assert back["resume"] is True and back["no_roles"] is True
    assert "no_meta" not in back, "an unchecked box emits no flag and comes back absent"
    assert back["extra"] == "--pull-apk"
    assert back["merge"] == "old.json" and back["strict_period"] is True


def test_settings_to_args_omits_empty_fields():
    cap, par = esmo.settings_to_args({"range": "", "limit": "", "extra": ""})
    assert cap == [] and par == []


def test_builtin_presets_survive_a_gui_roundtrip():
    """Order changes - the GUI emits fields in widget order - but nothing may be lost."""
    for name, flags in esmo.PRESETS.items():
        cap, _ = esmo.settings_to_args(esmo.args_to_settings(flags))
        assert sorted(cap) == sorted(flags), name


def test_unknown_flags_survive_in_the_extras_box():
    s = esmo.args_to_settings(["--pull-apk", "--adb", "C:\\tools\\adb.exe"])
    assert s["extra"] == "--pull-apk --adb C:\\tools\\adb.exe"
    cap, _ = esmo.settings_to_args(s)
    assert cap == ["--pull-apk", "--adb", "C:\\tools\\adb.exe"]


def test_quoted_windows_path_in_extras_stays_one_argument():
    assert esmo.split_extra('--adb "C:\\Program Files\\adb.exe"') == \
        ["--adb", "C:\\Program Files\\adb.exe"]


def test_a_flag_value_is_not_mistaken_for_a_preset():
    """`esmo.py --range 14d` must be a custom run, not a preset called 14d."""
    assert esmo.take_preset(["--range", "14d"]) == (None, ["--range", "14d"])
    assert esmo.take_preset(["weekly", "--limit", "3"]) == ("weekly", ["--limit", "3"])
    assert esmo.take_preset([]) == (None, [])
