# CS strategy pipeline - design

Status: **designed, not built.** Nothing here exists yet. The mapping session is done
(37 screens of the Inferno T-side editor, 2026-08-30) and it came back green, so the
remaining unknowns are small.

Goal: keep a map's strategy book in a file (StratBook already authors these as YAML), and
have the toolkit enter it into the game's CS strategy editor instead of doing it by hand.

Feasible: the write side has precedent. `probe/esmo_capture_tactics.py` already walks the
Carball tactic editor and mutates the open preset, and `esmo_capture.py` supplies the
plumbing (`Adb.tap`, `dump_xml`, `wait_until_stable`, `signature`).

## What the mapping session established

**The editor is fully text-matchable.** Callout names are drawn as labels over the minimap,
but each one is its own clickable node with exact bounds, so they are matched by text like
everything else in this app. No hand-measured coordinate table, which was the failure mode
that would have killed the project.

```
* [827,116][900,161]   'B Dark'
* [827,141][996,186]   'B Bombsite (default)'
* [962,289][1112,334]  'Banana Sandbags'
```

Inferno publishes 64 of them. Every picker is a modal dialog carrying a full-screen
`'Dismiss'` node, so backing out is one tap and is verifiable.

**The hierarchy** is Map > Side (T/CT) > Strategy > tactic per player (added one at a time
with `Add player`) > tasks. A `plans` entry in the YAML is a tactic.

**Task types and their fields:**

| Task type | Fields |
| --- | --- |
| Movement (the YAML's `GOTO`) | Destination, Tempo |
| Defense (the YAML's `HOLD_ANGLE`) | Position, Target, Duration |
| Utility | Utility, Position, Target, Critical utility flag |
| Drop Item | Item, Target (a player selector) |

Enumerations, verbatim from the game:

- Tempo: `Slowest` `Slow` `Situational` `Fast` `Fastest`
- Duration: `Until a tactical change` `Fixed duration` `Until a synchronization point`
  `Until an enemy interaction` `Until a player state`
- Enemy interaction: `Enemy Spotted` `Enemy Killed`
- Player state: `Health` `Blindness`
- Utility: `Decoy Grenade` `Flashbang` `Frag Grenade` `Smoke Grenade` `Molotov`
- Drop item: `C4` `Sniper` `Flashbang` `HE Nade` `Smoke Grenade` `Molotov`
- Player type: `Specific` `Situational` `Bomb carrier` `Label holder`

Cost: a Movement task is about 7 taps, a Defense task 9 to 11. A ~300 task book is roughly
2400 taps, near an hour per map.

### Three corrections to the hand-written YAML

1. **`tempo: NORMAL` does not exist.** The game has no such option. Every `NORMAL` in
   `mirage_default_strategies.yaml` is invalid and presumably means `Situational`. This is
   the exact class of error the offline validation step exists to catch, and it is in the
   data today.
2. **`lock` is real but misplaced.** It is not a sibling of `tempo`, it is a *duration mode*:
   Duration > "Until a synchronization point" > a picker of named points (Alpha, Bravo,
   Charlie ...). The "synchronizes with nobody" warnings in the editor are the game noting a
   point used by a single tactic.
3. **Callout names are display strings, not enums.** `B_BOMBSITE_DEFAULT` is
   `'B Bombsite (default)'` in game. Normalising is mechanical, and the captured vocabulary
   is the authority. The YAML was derived from real names, not invented.

`playerSelector` is real and sits on the tactic, not the task, alongside a "Critical player"
toggle and a label-tag picker. It stays out of v1 only because it never varies in the
existing files.

### The one thing that did not come back clean

**The Target picker is filtered by the chosen Position.** With Position `T Spawn`, Target
offered exactly `T Spawn` and `Tetris`. Legal position/target pairs are therefore not
derivable from a flat callout list, so full offline validation of a Defense task's target
means capturing the pairs: open the Target picker once per position, 64 extra picker walks
per map.

Worth paying. It is one-time, roughly ten minutes on the capture side, and it preserves the
rule that the writer never touches the device with a file that can fail.

## Shape

Same split as the champion pipeline, for the same reason. No parsing in the capture script,
no device access in the parser.

| Script | Does |
| --- | --- |
| `esmo_capture_strats.py` | Walks one map's strategy editor. Dumps raw XML for every picker, including the per-position Target lists. Manifest records which map. |
| `parse_strats.py` | Raw XML to `strat_vocabulary.json` (callouts, task types, tempos, durations, legal position/target pairs) and `strategies.json` (the book as it exists in game). |
| `esmo_apply_strat.py` | A strategy file to the device. |

Capture is scoped to whichever map's editor is open, one map per run. Walking all seven maps
up front would multiply every unknown by seven before one has been validated.

**Reader and writer share one schema.** `strategies.json` is directly re-appliable. That makes
the verification diff a plain dict comparison, and it gives "export the game's own book to
seed StratBook" for free.

## Schema v1

```
map, label, side, category, plans[ { label, tasks[ <task> ] } ]

<task> is one of:
  { type: MOVEMENT,  destination, tempo }
  { type: DEFENSE,   position, target, duration }
  { type: UTILITY,   utility, position, target, critical? }
  { type: DROP_ITEM, item, target }

duration is one of:
  { mode: TACTICAL_CHANGE }
  { mode: SYNC_POINT, point }
  { mode: ENEMY_INTERACTION, interaction }
  { mode: PLAYER_STATE, state }
```

Deliberately absent, add them when there is evidence they carry information:

- `buyModes` and `playerSelector` are identical on every strategy in every existing file.
  Reproducing a constant would cost about 70 extra picker interactions per map.
- `Fixed duration` takes a typed number, and it is the only field in the whole editor that
  needs an `input text` primitive the toolkit does not have. Left out until something needs
  it.

YAML is accepted alongside JSON, with `import yaml` done lazily only for a `.yaml` path.
Same treatment Pillow already gets in the capture path: optional at runtime, clear error
when missing.

## Writer contract

Each rule below exists because of a specific failure it prevents.

- **Validate the whole file offline first**, against the captured vocabulary, and refuse to
  connect if anything is unknown. A typo fails in 0.1s instead of half-building a strategy 20
  minutes into a run. `tempo: NORMAL` is the worked example.
- **`plan_taps(strategy, vocabulary) -> [Action]` is pure**, and lives where the offline
  suite can import it. The executor only walks the list. This is what makes the thing
  testable without an emulator, the same way `settings_to_args` keeps tkinter out of the
  tests. `--dry-run` is then just printing the plan.
- **Dry run by default**, `--apply` to commit.
- **Creates new strategies only.** Never edits, never deletes. Delete is the only
  irreversible action in the app, and old versions from an iterate loop get removed by hand.
  Automating three taps is not worth owning that risk.
- **`map` must match the editor screen** or the run refuses. One string comparison against a
  label the accessibility tree already exposes (`Inferno / T Side`), guarding the worst
  failure mode: 2400 silently wrong taps from applying a Mirage book to Inferno.
- **Abort on the first failure**, naming the half-built strategy so it can be deleted. No
  resume: a partially entered task is not representable, so resuming would mean reading the
  strategy back and guessing how deep the tap sequence got.
- **Re-read and diff against the input at the end.** Per-tap dumps would roughly double
  runtime and catch nothing the end diff misses, and the diff can say *which* task drifted.

## Not building

Resume, GUI button, launcher preset, delete automation, per-tap verification dumps, typed
numeric input. The GUI's contract ("widgets are the truth, the preview is the filename")
describes capture runs; a file-driven mutating action is a different shape and does not
belong there.

## Trap worth remembering

While the detail pane is open, every node in the dump has `x >= 741`: Flutter suppresses the
covered route's semantics, so the left-hand strategy list is simply absent from the tree.
Capture has to walk one pane at a time and must not read an empty left pane as an empty book.
