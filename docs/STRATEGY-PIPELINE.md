# CS strategy pipeline - design

Status: **designed, not built.** Nothing here exists yet. The mapping session is done
(37 screens of the Inferno T-side editor, 2026-08-30) and it came back green, so the
remaining unknowns are small.

Goal: keep a map's strategy book in a file, and have the toolkit enter it into the game's
CS strategy editor instead of doing it by hand.

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

Inferno publishes 63 of them, and the Destination and Position pickers offer the same list.
Every picker is a modal dialog carrying a full-screen `'Dismiss'` node, so backing out is one
tap and is verifiable.

**The hierarchy** is Map > Side (T/CT) > Strategy > tactic per player (added one at a time
with `Add player`) > tasks.

**Task types and their fields:**

| Task type | Fields |
| --- | --- |
| Movement | Destination, Tempo |
| Defense | Position, Target, Duration |
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

A synchronization point is not a task attribute, it is a duration mode: Duration > "Until a
synchronization point" > a picker of named points (Alpha, Bravo, Charlie ...). The editor
warns when a point is used by a single tactic, since then it synchronizes with nobody.

Cost: a Movement task is about 7 taps, a Defense task 9 to 11. A ~300 task book is roughly
2400 taps, near an hour per map.

### The one thing that did not come back clean

**The Target picker is filtered by the chosen Position.** With Position `T Spawn`, Target
offered exactly `T Spawn` and `Tetris`, out of 63 callouts on the map. So legal
position/target pairs cannot be derived from the callout list, and full offline validation of
a Defense task's Target means capturing them: open the Target picker once per position, 63
extra picker walks per map.

Worth paying. It is one-time, roughly ten minutes on the capture side, and it preserves the
rule that the writer never touches the device with a file that can fail.

Observed on the one position mapped so far: a position appears in its own Target list.

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
the verification diff a plain dict comparison, and it gives "export the game's own book" for
free.

Parser note: the picker dumps carry a status-bar node at `[0,0][1920,12]` that is chrome, not
a callout. Filter by height.

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

Deliberately absent, add them when something needs them:

- The tactic-level player Type and the "Critical player" toggle. v1 sets tasks and labels and
  leaves those at the game's defaults, which saves about 70 picker interactions per map.
- `Fixed duration` takes a typed number, and it is the only field in the whole editor that
  needs an `input text` primitive the toolkit does not have.

Input is JSON, which is stdlib. No other input format, so the toolkit gains no dependency
here.

## Writer contract

Each rule below exists because of a specific failure it prevents.

- **Validate the whole file offline first**, against the captured vocabulary, and refuse to
  connect if anything is unknown. A typo fails in 0.1s instead of half-building a strategy 20
  minutes into a run.
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
  failure mode: 2400 silently wrong taps from applying one map's book to another.
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
