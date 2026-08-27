"""Checks for the preset launcher. Builds command lines only, runs nothing."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import esmo  # noqa: E402


def test_preset_flags_come_first_then_passthrough():
    capture, _ = esmo.commands("weekly", ["--redo", "Brewer"], ".", "20260827")
    assert capture[2:] == ["--range", "7d", "--resume", "--scroll-method", "pagekeys",
                           "--redo", "Brewer"]


def test_parse_reads_the_folder_capture_writes_to():
    """esmo_capture.py writes beside itself, so the parser must be pointed at it."""
    _, parse = esmo.commands("weekly", [], ".", "20260827")
    assert parse[parse.index("--dir") + 1] == str(esmo.CAPTURE_DIR)
    assert esmo.CAPTURE_DIR.parent == esmo.CAPTURE.parent


def test_output_is_datestamped_in_the_out_dir(tmp_path):
    _, parse = esmo.commands("daily", [], tmp_path, "20260827")
    assert parse[parse.index("--out") + 1] == str(tmp_path / "20260827_champions.json")


def test_every_preset_has_a_description():
    assert set(esmo.PRESETS) == set(esmo.DESCRIPTIONS)
