# ESMO data toolkit

[![CI](https://github.com/T0wby/EsmoToolkits/actions/workflows/ci.yml/badge.svg)](https://github.com/T0wby/EsmoToolkits/actions/workflows/ci.yml)

Extracts champion data from **ESMO: Esports Manager Online** - abilities, base stats, live
win rates, matchups and portraits - into structured JSON.

ESMO is a mobile-only game with no public API. This toolkit runs the app in an Android
emulator and reads its **accessibility tree**, which Flutter apps publish in full. That
means exact text, not OCR: every number you see below came out of the app as a string.

Built and validated against **app v1.1.4**, 72 champions, 105 champion-positions.

```
EsmoToolkits/
├── README.md                      this file - setup, per-script guides, data schema
├── requirements.txt
├── pyproject.toml                 packaging - `pip install .` for the esmo-* commands
├── docs/
│   └── EXTRACTION-GUIDE.md        how the technique works, and how to extend it
├── scripts/
│   ├── esmo_capture.py            walks the champion roster, saves raw dumps
│   ├── parse_esmo.py              turns raw dumps into champions.json
│   └── esmo_explore.py            maps any other page in the game
├── examples/
│   └── champions.sample.json      2 champions, so you can see the output shape
└── tests/
    └── test_parse.py              offline parser checks - no emulator needed
```

---

## Contents

1. [What you get](#1-what-you-get)
2. [Setup](#2-setup)
3. [Quick start](#3-quick-start)
4. [`esmo_capture.py`](#4-esmo_capturepy)
5. [`parse_esmo.py`](#5-parse_esmopy)
6. [`esmo_explore.py`](#6-esmo_explorepy)
7. [Data schema](#7-data-schema)
8. [Common workflows](#8-common-workflows)
9. [Troubleshooting](#9-troubleshooting)
10. [Limitations](#10-limitations)
11. [Before you use this](#11-before-you-use-this)

---

## 1. What you get

Per champion:

- **Identity** - name, class, class description, attack type, damage type, portrait
- **Base stats** - ~24 values: health, resource, attack damage, auto-attack timings,
  ability power, crit, armor, magic resist, penetration, tenacity, lifesteal, move speed
- **Abilities** - all four (Q/W/E/R) with targeting type, scaling, effects, cooldown, range
- **Per position** - champions play up to three positions, each with its own:
  pick/ban/win rate, W/L, games, KDA, gold/CS/damage per minute, vision,
  and best/worst matchups with win rate and sample size

A full run produces 72 champions · 288 abilities · 1728 stat values · ~106 positions ·
~570 matchups. See `examples/champions.sample.json` for the exact shape.

---

## 2. Setup

**1. BlueStacks 5**, with ESMO installed and logged in.
Set the instance to **1920×1080** - every coordinate in these scripts was measured there,
and the capture script warns you if it isn't.

**2. Enable ADB**: BlueStacks Settings → Advanced → **Android Debug Bridge**. Note the
address it shows (e.g. `127.0.0.1:5555`); newer builds randomise the port.

**3. adb** - either [Android platform-tools](https://developer.android.com/tools/releases/platform-tools)
unzipped somewhere permanent, or BlueStacks' own copy at
`C:\Program Files\BlueStacks_nxt\HD-Adb.exe`. The scripts find either automatically.

**4. Python 3.9+** and one dependency:

```powershell
pip install -r requirements.txt      # Pillow, for portrait cropping and icon-state reading
```

Pillow is optional - without it you lose portraits and position detection falls back to a
less reliable method. Install it.

Or install the toolkit itself, which pulls in Pillow and adds the commands
`esmo-capture`, `esmo-parse` and `esmo-explore` to your PATH:

```powershell
pip install .          # add -e for an editable install
```

Every `python scripts/x.py` below then also works as the matching command.

The parser has offline tests that need no emulator - `pip install -e ".[dev]"`, then
`ruff check .` and `pytest -q`. CI runs both on 3.9 and 3.13.

**While a capture runs:** leave the BlueStacks window **restored, not minimised**. Input
injection doesn't need focus, so you can use your PC normally, but a minimised emulator can
return blank frames and the pixel-based checks then fail. Don't click inside the BlueStacks
window while it's running, and stop your machine sleeping.

---

## 3. Quick start

Open ESMO on **Playbook → Heroes** (the champion grid), then:

```powershell
python scripts/esmo_capture.py --limit 3     # ~5 min sanity check
python scripts/esmo_capture.py               # full roster, ~3 h
python scripts/parse_esmo.py                 # champions.json, seconds
```

Always run the `--limit 3` check first. Confirm each champion prints sensible positions and
a `date range applied:` line before committing three hours.

---

## 4. `esmo_capture.py`

Walks every champion and saves the **raw accessibility dumps**. It deliberately does no
parsing - that is `parse_esmo.py`'s job, so a parsing mistake never costs another capture.

### How it works

The champion grid is images with no text, so a champion can only be identified by opening
it. For each one the script opens the detail page, captures the **Overview**, **Meta** and
**Stats** tabs, scrolls each to the end saving every distinct screen, visits every position
the champion can play, crops the portrait, then backs out and re-establishes its grid
position. Progress is written after every champion.

### Usage

```powershell
python scripts/esmo_capture.py [options]
```

| Flag | Default | Description |
|---|---|---|
| `--range {24h,7d,28d,any,none}` | `7d` | Meta date window, re-applied per champion (the app forgets it). `any` needs a subscription. `none` leaves the app's own 28-day window. |
| `--resume` | off | Skip champions already captured in this folder |
| `--redo NAMES` | – | Comma-separated champions to re-capture from scratch; their existing data is deleted first |
| `--limit N` | – | Stop after N champions |
| `--phase {top,bottom,both}` | `both` | Which grid scroll position to walk. `bottom` reaches the last rows without re-walking the first |
| `--no-meta` | off | Skip the Meta tab entirely - abilities, stats and portraits only (~15 min) |
| `--no-roles` | off | Capture only the default position, not all of them |
| `--pull-apk` | off | Also pull the app's APKs (for full-resolution art) |
| `--scroll-method NAME` | auto | Force a scroll gesture. `pagekeys` is the one that works |
| `--port N` | auto | ADB port |
| `--adb PATH` | auto | Path to the adb executable |
| `--no-zip` | off | Don't zip the output at the end |
| `--probe-roles` | – | Diagnostic: measure the position selector on the open champion |
| `--probe-filter` | – | Diagnostic: explore the date-range picker on the open champion |

### Output

```
esmo_capture/
├── raw/<Champion>/
│   ├── overview_00.xml …           each scroll position of the Overview tab
│   ├── meta_<Position>_00.xml …    one set per position played
│   └── stats_00.xml …
├── portraits/<Champion>.png        120×120 crop
├── portraits/_rolestrip_<X>.png    position-selector strip, for verification
├── captured.json                   manifest: what was captured, and the grid layout
└── apk/                            only with --pull-apk (excluded from the zip)
```

Plus `esmo_capture.zip` of everything except the APKs.

### Reading the console

```
  [ 16] Engineer      ov=2 meta=12 in Mid/Top/Support  stats=3 (40.4m)
```

Champion number · dumps per tab · positions found · elapsed. Anything appended after
`INCOMPLETE:` is a problem on that champion:

| Marker | Meaning |
|---|---|
| `date-range` | The date filter didn't apply - that champion is in the wrong window |
| `meta:<Position>` | That position's tab didn't scroll to the end |
| `roles-unverified` | The position selector couldn't be read (usually a minimised window) |

At the end it lists affected champions and prints a ready-made `--redo` command.

### Resume and redo

`--resume` skips a champion only when what's on disk **covers what the current run wants**,
so a `--no-meta` pass followed by a full pass correctly re-visits everything, instead of
seeing the name and calling it done.

`captured.json` also stores which champion sits in which grid cell, so later resumes skip
finished champions without opening them. The map is validated on first mismatch and
abandoned if the roster has shifted.

---

## 5. `parse_esmo.py`

Converts a capture folder into `champions.json`. Runs entirely offline, so re-run it freely:
if parsing needs fixing, that's seconds rather than another three-hour walk.

```powershell
python scripts/parse_esmo.py [options]
```

| Flag | Default | Description |
|---|---|---|
| `--dir DIR` | `esmo_capture` | Capture folder to read |
| `--out FILE` | `champions.json` | Output file |
| `--merge FILE` | – | Fill positions/meta from an existing `champions.json` for champions captured without meta |
| `--strict-period` | off | Drop positions whose date window differs from the majority |

### Reading the summary

```
parsed 72 champions -> champions.json
  date ranges seen:
    Aug 7 - Aug 14         106 position(s)
  abilities: 288   matchups: 577   stat values: 1728
  positions: 106
```

Checks worth making every time:

- **One date range only.** Two entries means mixed windows and it says so explicitly -
  those rows are not comparable to each other.
- **`abilities` = 4 × champion count.** Anything less means a truncated Overview tab.
- **`positions` ≈ 106** for a full roster.

It also reports anything it repaired: stale files ignored, phantom positions dropped,
champions missing meta.

### What it cleans up

The app has several behaviours that produce plausible-but-wrong data, and the parser
defends against each:

- **Placeholder meta.** The Meta tab paints a cached `13W 12L · 25 games` block before real
  data arrives. The parser keeps the largest sample and ties pick/ban rate to the same dump
  as the win rate, so the two can't be mixed.
- **Phantom positions.** Tapping a position a champion can't play does nothing, leaving the
  previous position's data on screen. Positions whose W/L/games exactly duplicate an earlier
  one are dropped - the first occurrence is the real one.
- **Stale files.** `captured.json` is the authority on which positions exist; files for
  anything else are leftovers from an earlier run and are ignored.
- **Section-scoped stats.** `ARMOR/Armor` and `PENETRATION/Armor` are different values with
  the same label, so stat keys are scoped by their section header.

---

## 6. `esmo_explore.py`

Maps **any** page in the game. Use it to extend this toolkit beyond champions - Items,
Draft, Strategies, Competitions, team and player pages are all unmapped.

Navigate to a page in BlueStacks, then:

```powershell
python scripts/esmo_explore.py [--port N] [--adb PATH] [--no-png]
```

It prints every element with a ready-to-use tap coordinate:

```
  --- 01_items_page
   * tap( 833,  78) [797,42][869,114]    Button  'Back'
     tap(1037, 168) [959,144][1116,192]  View    'Longsword'
   * tap( 927, 408) [821,384][1033,432]  Button  '1300 gold'
   -- 3 text node(s); * = tappable
```

| Command | Action |
|---|---|
| `<Enter>` | Snapshot the current screen |
| `<any text>` | Snapshot, labelled with that text |
| `t <x> <y>` | Tap, then snapshot |
| `d` / `u` | Page down / page up, then snapshot |
| `scan [label]` | Scroll to top, then page to the end capturing everything |
| `b` | Back, then snapshot |
| `find <text>` | Search the last snapshot, print matches with coordinates |
| `raw` | Re-print including layout-only nodes |
| `q` | Quit and zip |

A routine that works on any page: snapshot the landing state → `scan` to see what's below
the fold → tap into one item and snapshot → tap each filter and snapshot → `q`. The
resulting `esmo_explore.zip` contains everything needed to write a capture script for that
page. `docs/EXTRACTION-GUIDE.md` covers this in depth.

---

## 7. Data schema

Top level:

| Field | Type | Description |
|---|---|---|
| `source` | string | Always `"ESMO in-game capture"` |
| `champion_count` | int | Number of champions |
| `with_meta` | int | How many have position/meta data |
| `champions` | array | See below |

Per champion:

| Field | Type | Description |
|---|---|---|
| `name` | string | e.g. `"Automata"` |
| `class` | string | e.g. `"Battle Mage"` - 12 distinct classes |
| `class_description` | string | The class's in-game description |
| `attack_type` | string | `"Ranged"` or `"Melee"` |
| `damage_type` | string | `"Magic"`, `"Physical"`, `"Mixed"`, `"True"` |
| `base_health` | string | e.g. `"694 (+120/lvl)"` - kept as the game formats it |
| `base_resource` | string \| null | Null for resourceless champions |
| `resource_type` | string \| null | e.g. `"Mana"` |
| `abilities` | array | Four entries, Q/W/E/R |
| `positions` | array | One entry per position played (0–3) |
| `meta` | object | Same shape as a position entry; mirrors the first position |
| `stats` | object | Section → label → value |
| `portrait` | string \| null | Relative path to the crop |
| `has_meta` | bool | False after a `--no-meta` capture |
| `meta_source` | string | Present only when filled via `--merge` |

Ability:

| Field | Type | Description |
|---|---|---|
| `slot` | string | `Q`, `W`, `E`, `R` |
| `targeting` | string | e.g. `"Line skillshot"`, `"Dash + hit"`, `"Ground zone"` |
| `scaling` | string \| null | e.g. `"80 (+47/lvl)"` |
| `effects` | array | e.g. `["Slow 35% · 1.50s"]` |
| `cooldown` | string \| null | e.g. `"6s"` |
| `range` | number \| null | Null for global abilities |
| `raw` | string | The unparsed node, so nothing is ever lost |

Position:

| Field | Type | Description |
|---|---|---|
| `position` | string | `Top`, `Jungle`, `Mid`, `Bot`, `Support` |
| `period` | string | Date window, e.g. `"Aug 7 - Aug 14"` |
| `pick_rate`, `ban_rate` | number | Percent. Champion-level, not position-level |
| `win_rate` | number | Percent |
| `wins`, `losses`, `games` | int | Sample size |
| `kda`, `gold_per_min`, `cs_per_min`, `damage_per_min`, `vision_per_game` | number | Averages |
| `best_matchups`, `worst_matchups` | array | `{opponent, win_rate, games}` - counts vary, sometimes zero |
| `sample_warning` | string | Present if more than one sample size was seen |

Numbers with per-level scaling stay as strings (`"694 (+120/lvl)"`) rather than being split,
so nothing is lost to a parsing assumption. Split them downstream if you need to.

---

## 8. Common workflows

**Weekly refresh.** Keep the folders separate - one window per folder.

```powershell
Rename-Item esmo_capture esmo_capture_lastweek
python scripts/esmo_capture.py --range 7d
python scripts/parse_esmo.py --out champions_weekly.json
```

**Both windows.** 28-day for stable numbers, 7-day for trend.

```powershell
python scripts/esmo_capture.py --range 28d
Rename-Item esmo_capture esmo_capture_28d
python scripts/esmo_capture.py --range 7d
Rename-Item esmo_capture esmo_capture_7d
python scripts/parse_esmo.py --dir esmo_capture_28d --out champions_28d.json
python scripts/parse_esmo.py --dir esmo_capture_7d  --out champions_7d.json
```

Weekly samples run roughly a quarter of monthly. Suppress trend deltas below ~100 games and
hide matchups below ~20 - a 3-0 matchup reads as 100% and means nothing.

**Fast static refresh** after a balance patch, when only abilities and stats changed:

```powershell
python scripts/esmo_capture.py --no-meta
python scripts/parse_esmo.py --merge champions_weekly.json
```

Positions come from the Meta tab, so a `--no-meta` capture has none. `--merge` fills them
from a previous file, tagging each with `meta_source` and keeping the original `period` so
older meta is never mistaken for fresh.

**Fix a few champions** without redoing the roster:

```powershell
python scripts/esmo_capture.py --resume --redo Brewer,Nomad,Volt
```

`--redo` implies `--resume` - the named champions are re-walked and the rest of the
roster is skipped. Match `--range` to the folder you're patching.

---

## 9. Troubleshooting

**"No adb device"** - BlueStacks isn't running, or ADB isn't enabled in its settings. Pass
the port explicitly: `--port 5555`.

**"Could not find adb"** - pass it: `--adb "C:\platform-tools\adb.exe"`.

**Every scroll gesture reports "no effect"** - this is expected for the swipe variants;
`pagekeys` should report `MOVED`. If *nothing* moves, the emulator isn't receiving input -
check BlueStacks is at 1920×1080 and ESMO is in the foreground.

**"The emulator is returning a blank/dark frame"** - the window is minimised. Restore it.
It doesn't need focus.

**Champions captured in the wrong date window** - the run prints the affected names and a
`--redo` command. Re-run that.

**Parser says "MIXED WINDOWS"** - the folder holds two capture dates. Either use
`--strict-period` to keep only the majority, or `--redo` the odd ones.

**A run stops short of 72 champions** - the grid didn't scroll. The script now reports this
rather than finishing quietly; re-run with `--resume --phase bottom`.

---

## 10. Limitations

**Not extractable - these are pixels, not text:**

- Champion art and role icons (screenshot crops, or `--pull-apk` for the bundled originals)
- The **early-game advantages chart** - its accessibility label gives the axes
  (`0m…20m`, `Gold`, `CS`, `XP`) but no plotted values

**Fragile to app updates.** Coordinates were measured on v1.1.4 at 1920×1080. A UI redesign
means re-mapping with `esmo_explore.py`. Text-labelled controls are matched by their text
and survive layout changes; icon-only controls are not.

**Timing-dependent.** The Meta tab loads asynchronously and the toolkit waits for it. On a
slow machine or connection, expect longer runs rather than wrong data - the checks are built
to fail loudly, not silently.

**Pick and ban rate appear to be champion-level**, not position-level: a champion reports
the same figure across all its positions. Win rate, KDA and matchups are genuinely
per-position.

---

## 11. Before you use this

This reads the app's own screen the way an accessibility tool does: it makes no calls to
ESMO's backend, doesn't modify or repackage the client, and doesn't bypass authentication.
It does what a person clicking through the app would do, unattended.

That said, **automated extraction may not be permitted by ESMO's terms of service**, and the
developers have not been asked. If you plan to publish or share data produced with this,
ask them first - their Discord is linked from [esmo.gg](https://esmo.gg/). A studio-provided
export would be better than this toolkit in every respect: no emulator, no coordinates, and
it stays correct across patches.

Be considerate regardless. A full run is ~72 champions over about three hours, slower than a
person browsing. Don't run it in a loop.
