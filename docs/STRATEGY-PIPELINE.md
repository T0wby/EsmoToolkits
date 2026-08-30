# CS strategy pipeline - design

Status: **designed, not built.** Nothing here exists yet. Blocked on one mapping session
at the emulator (see "Next action" at the bottom).

Goal: keep a map's strategy book in a file (StratBook already authors these as YAML), and
have the toolkit enter it into the game's CS strategy editor instead of doing it by hand.

Feasible: the write side has precedent. `probe/esmo_capture_tactics.py` already walks the
Carball tactic editor and mutates the open preset, and `esmo_capture.py` supplies the
plumbing (`Adb.tap`, `dump_xml`, `wait_until_stable`, `signature`). Expect roughly 30 to 60
minutes unattended per map: about 300 tasks at 4 to 6 taps each, plus stability waits.

## Shape

Same split as the champion pipeline, for the same reason. No parsing in the capture script,
no device access in the parser.

| Script | Does |
| --- | --- |
| `esmo_capture_strats.py` | Walks one map's strategy editor. Dumps raw XML, including the callout and tempo pickers. Manifest records which map. |
| `parse_strats.py` | Raw XML to `strat_vocabulary.json` (the real per-map callouts, task types, tempos) and `strategies.json` (the book as it exists in game). |
| `esmo_apply_strat.py` | A strategy file to the device. |

Capture is scoped to whichever map's editor is open, one map per run. Walking all seven maps
up front would multiply every unknown by seven before one has been validated.

**Reader and writer share one schema.** `strategies.json` is directly re-appliable. That makes
the verification diff a plain dict comparison, and it gives "export the game's own book to
seed StratBook" for free. It also produces the first genuinely useful output of the project:
a diff between the game's real vocabulary and the hand-written YAML files, whose enums
(`A_STAIRS`, `HOLD_ANGLE`, `FASTEST`) appear nowhere outside those files and are therefore
unverified.

## Schema v1

```
map, label, side, category, plans[ { label, tasks[ { type, callout | from/to, tempo } ] } ]
```

Deliberately absent, add them when there is evidence they carry information:

- `buyModes` and `playerSelector` are identical on every strategy in every existing file.
  Reproducing a constant would cost about 70 extra picker interactions per map.
- `lock` does vary, but it is the one field that looks generated rather than observed
  (a quoted string with a numeric suffix in a file where everything else is an unquoted
  enum). The mapping session decides whether the editor has such a control at all.

YAML is accepted alongside JSON, with `import yaml` done lazily only for a `.yaml` path.
Same treatment Pillow already gets in the capture path: optional at runtime, clear error
when missing.

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
  label the accessibility tree already exposes, guarding the worst failure mode: 300 silently
  wrong taps from applying a Mirage book to Inferno.
- **Abort on the first failure**, naming the half-built strategy so it can be deleted. No
  resume: a partially entered task is not representable, so resuming would mean reading the
  strategy back and guessing how deep the tap sequence got.
- **Re-read and diff against the input at the end.** Per-tap dumps would roughly double
  runtime and catch nothing the end diff misses, and the diff can say *which* task drifted.

## Not building

Resume, GUI button, launcher preset, delete automation, per-tap verification dumps. The GUI's
contract ("widgets are the truth, the preview is the filename") describes capture runs; a
file-driven mutating action is a different shape and does not belong there.

## Next action

An `esmo_explore.py` session on the CS strategy editor. It answers:

1. Is the callout picker matchable by accessibility text, or is it a tap on the minimap?
   Text matching survives layout changes and is cheap. Minimap taps would mean a hand
   measured pixel table of ~25 callouts per map across seven maps, and would make this not
   worth building.
2. Same question for `from` and `to` on `HOLD_ANGLE`, and for the plan and strategy labels
   (free text would need an `input text` primitive the toolkit does not have).
3. Does a `lock` control exist?
4. How many taps does one task actually cost?

Everything downstream is guesswork until that dump exists.
