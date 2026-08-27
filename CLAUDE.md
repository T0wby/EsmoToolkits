# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Four standalone Python scripts (no package, no `__init__.py`) that extract champion data from
ESMO: Esports Manager Online by reading Android's accessibility tree through `adb`, against the
game running in BlueStacks at 1920x1080. `pyproject.toml` exposes them as console scripts
(`esmo`, `esmo-capture`, `esmo-parse`, `esmo-explore`) with `package-dir = scripts/`.

## Commands

```bash
pip install -e ".[dev]"
pytest -q                       # full suite; offline, no emulator needed
pytest tests/test_parse.py -q   # one file
pytest -k matchup -q            # one test
ruff check .                    # lint (CI runs both, on py3.9 and py3.13)
```

Running the toolkit (needs BlueStacks + ESMO on Playbook > Heroes; a full capture is ~3h):

```bash
python scripts/esmo.py weekly -n    # print the two commands a preset issues, run nothing
```

`-n` is the safe way to check launcher behaviour without an emulator.

## Architecture

**Capture raw, parse offline.** This split is the load-bearing decision of the project.
`esmo_capture.py` only saves raw uiautomator XML dumps and portrait crops; `parse_esmo.py`
turns a folder of those into `champions.json` with no device involved. A parser bug is fixed
by re-running the parser, never by repeating a 3-hour walk. Keep it that way: no parsing logic
in the capture script, no device access in the parser.

Pipeline:

- `esmo_explore.py` - interactive mapper for an unmapped screen; prints every node with a tap
  coordinate. Use it before writing capture code for a new page.
- `esmo_capture.py` - walks the champion grid, opens each champion, dumps Overview/Meta/Stats
  tabs across every scroll position and every position the champion plays, writes
  `esmo_capture/` (`raw/<Champion>/*.xml`, `portraits/`, `captured.json`).
- `parse_esmo.py` - reads that folder, emits `champions.json`.
- `esmo.py` - preset launcher; `PRESETS`/`DESCRIPTIONS` map a name to capture flags, then runs
  capture and parse in sequence. Also the home of every pure function the GUI needs.
- `esmo_gui.py` - a Tkinter window over `esmo.py`. It builds an `esmo.py` argv and spawns it
  in a new console (`CREATE_NEW_CONSOLE`, via `getattr` so non-Windows degrades to a normal
  spawn); it never captures or parses itself.

Both capture and explore anchor output at `pathlib.Path.cwd()`, not next to the script - an
installed copy lives in site-packages. `esmo.py:CAPTURE_DIR` must stay in sync with
`esmo_capture.py:OUT`; a test asserts it. The GUI's "capture folder" field is passed as the
subprocess `cwd`, which is the only reason a window launched from a shortcut captures
anywhere sensible.

`~/.esmo.json` holds user presets (layered over the built-in `PRESETS`, config wins on a name
clash) and, under `state`, the GUI's memory of its own widgets. A missing or malformed file is
never fatal - `load_config` returns `{}`.

Two rules keep the GUI honest, both learned by getting them wrong first:
- **Widgets are the truth.** The GUI passes flags but *not* the preset name, because
  `esmo.py` puts preset flags first, so naming the preset too would re-add a flag you had
  just unticked.
- **The preview is the filename.** The GUI resolves the pattern itself and passes a literal
  `--out`, so what the window shows is what gets written.

`esmo.py` also takes the preset name off argv itself (`take_preset`) rather than letting
argparse do it: an optional positional next to unknown flags claims the *value* of the first
flag it doesn't recognise, so `esmo.py --range 14d` would otherwise look for a preset called
`14d`.

`captured.json` is the manifest and the authority: which champions were captured, which
positions each really has, and which grid cell each sits in. The parser uses it to ignore
stale files from earlier runs (leftover `meta_<Position>_*.xml` for a position the champion
no longer plays would otherwise silently mix two capture dates into one dataset).

## Rules learned the hard way (docs/EXTRACTION-GUIDE.md has the full list)

- **`input swipe` does not scroll Flutter views under BlueStacks.** Seven variants were tested;
  all silently fail. `input keyevent 93/92` (PAGE_DOWN/UP) is the only gesture that works -
  hence `--scroll-method pagekeys` in every preset. `detect_scroll_method` still probes at
  runtime because a future BlueStacks may differ.
- **Never trust a scroll or a tap; verify content actually changed.** A run that assumed swipes
  worked captured 60 of 72 champions and reported success. `wait_until_stable` and the
  signature comparisons exist for this.
- **Placeholders look like real data.** The Meta tab paints a cached `13W 12L` block before real
  numbers arrive, identical for every champion. Wait for stability *and* require an expected
  pattern before saving; the parser prefers the largest sample as a backstop.
- **The date range does not persist** - the app reverts to "Last 28 days" on every champion, so
  `--range` is re-applied per champion and verified from the button label.
- **Icon-only controls carry state in pixels** (position selector: lit ~227 vs greyed ~125).
  Treat luminance as a hint, confirm against the data.

Coordinates in the geometry block at the top of `esmo_capture.py` were measured on ESMO v1.1.4
at 1920x1080. Prefer matching controls by their accessibility text where one exists - that
survives layout changes; hardcoded coordinates do not.

## Conventions

- Python 3.9-compatible (CI floor). Stdlib only apart from Pillow, which is optional at runtime
  in the capture path (portrait cropping degrades gracefully without it).
- Ruff is configured to real errors only (`E4,E7,E9,F`) - the scripts are deliberately terse and
  style rules would be noise. Line length 100.
- Comments explain *why*, especially where a check guards against one of the traps above. Don't
  strip them as redundant; each one is a run that was lost.
- Prose in README and docs avoids em-dashes.
- Tests are pure and offline - they build command lines, parse fixture XML, and round-trip
  `examples/champions.sample.json`. Anything needing adb is not unit-testable here.
- Nothing testable lives in `esmo_gui.py`. `settings_to_args` / `args_to_settings` (widget
  state <-> argv, inverses of each other) sit in `esmo.py` so the suite never imports tkinter.
