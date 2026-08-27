# Extracting data from ESMO — how it works, and how to do it for any page

Written after building the MOBA champion pipeline (72 champions, 106 positions, two date
windows). Everything here generalises: the champion pages were just the first target.

---

## 1. The core discovery

**ESMO is a Flutter app, and Flutter publishes its entire widget tree to Android's
accessibility layer.**

That single fact is what makes this whole thing possible. It means:

- Every visible string is readable as **exact text** — no OCR, no image recognition
- Every element carries **exact pixel bounds**, so tap targets are computed, not guessed
- It works on any screen in the game, not just the ones already mapped

One critical detail: the text lives in the **`content-desc`** attribute, *not* `text`.
The `text` attribute is empty everywhere in this app. Anything reading `text` sees nothing
and concludes the app is unreadable — which is the wrong conclusion.

```xml
<node text="" content-desc="Battle Mage&#10;Short-range mages who..."
      class="android.view.View" bounds="[815,384][1847,587]" clickable="false"/>
```

You get this tree with:

```
adb shell uiautomator dump /sdcard/ui.xml
adb shell cat /sdcard/ui.xml
```

---

## 2. Setup (one time)

1. **BlueStacks 5**, running ESMO, set to **1920×1080**. All coordinates below assume it.
2. **Enable ADB**: BlueStacks Settings → Advanced → Android Debug Bridge. Note the port.
3. **adb**: either Android platform-tools, or BlueStacks' own
   `C:\Program Files\BlueStacks_nxt\HD-Adb.exe`.
4. `pip install Pillow` — only needed for screenshot cropping and reading icon states.

The scripts auto-detect adb and the port. Pass `--port 5555` if detection misses.

**The emulator window must be restored — not minimised.** Input injection works without
focus, so you can use your PC normally, but a minimised window can return blank frames and
anything reading pixels then fails silently.

---

## 3. Mapping a new page

Use `esmo_explore.py`. Navigate to the page in BlueStacks, then:

```powershell
python scripts/esmo_explore.py
```

Type a label, press Enter, and it prints every element with a ready-to-use tap coordinate:

```
  --- 01_draft_screen
   * tap( 833,  78) [797,42][869,114]    Button  'Back'
     tap(1037, 168) [959,144][1116,192]  View    'Automata'
   * tap( 927, 408) [821,384][1033,432]  Button  'Jul 12 - Aug 9'
   -- 3 text node(s); * = tappable
```

Commands: `<Enter>` snapshot · `<text>` labelled snapshot · `t x y` tap · `d`/`u` page
down/up · `scan` capture a whole scrollable page · `b` back · `find <text>` locate an
element · `raw` include layout-only nodes · `q` quit and zip.

**A five-step routine that works for any page:**

1. **Snapshot the landing state.** What's text, what isn't?
2. **`scan`** — pages to the bottom capturing everything. Tells you how much is below the
   fold and whether the view scrolls at all.
3. **Tap into one item** and snapshot. Repeat for each tab or sub-view.
4. **Tap every filter/selector** and snapshot each state. Note which are text-labelled
   (reliable) versus icon-only (needs pixel reading — see trap 4).
5. **`q`**, then hand `esmo_explore.zip` to Claude for a capture script.

What decides whether a page is easy or hard:

- **Is the list itself labelled?** The champion grid is pure images with no names, so every
  cell had to be opened to identify it. A list with visible text names is far cheaper.
- **How many nested states?** Champions had 3 tabs × up to 3 positions × a date filter.
- **Does it paginate or scroll?** Scrolling is handled; pagination needs mapping.

---

## 4. The traps — each of these cost a full run

**1. Swipe gestures do not work. At all.**
`input swipe` never scrolls a Flutter view under BlueStacks, at any duration, in any screen
region. Seven variants were tested; all failed. **`input keyevent 93` (PAGE_DOWN) is the
only thing that scrolls**, with `92` (PAGE_UP) to go back. It moves less than a viewport, so
consecutive dumps overlap and nothing is skipped between them.

This applies to *every* scrollable view, including lists and grids. A run that used swipes
for the champion grid never left the top and silently captured 60 of 72 champions while
reporting success.

**Rule: never trust a scroll. Verify content actually moved** by comparing dumps, or by
checking that a known element scrolled out of view.

**2. Data loads asynchronously, and placeholders look like real data.**
The Meta tab paints a cached `13W 12L · 25 games` block — identical for every champion —
before the real numbers arrive. Dumping too early captures plausible, wrong data.

**Rule: wait for content to stop changing, and require a specific expected pattern to be
present, before saving anything.** Then have the parser prefer the largest sample as a
backstop, so a leak is corrected rather than silently kept.

**3. Ignored taps leave the previous state on screen.**
Tapping a disabled position selector did nothing, so the previous position's data stayed
visible and got recorded under the new position's name. It produced 17 phantom entries that
looked entirely legitimate.

**Rule: after any state-changing tap, verify the state actually changed.** Compare the new
content against what you already recorded; identical content means the tap was ignored.

**4. Icon-only controls carry state in pixels.**
Where controls have no text, brightness distinguishes them. On the position selector:
playable icons peak at luminance 227, greyed-out at ~125, and the selected one sits on a lit
background (~70 cell mean versus ~34).

**Rule: use pixel state as a hint, never as the authority** — a screenshot can be taken
before icons finish repainting. Confirm against the data.

**5. Stale files from previous runs parse as current data.**
Capturing into an existing folder leaves files for things that no longer exist, mixing two
capture dates in one dataset invisibly.

**Rule: purge before capture, sweep the whole folder at startup, and have the parser treat
the run manifest as the authority on what should exist.**

**6. Absent data is not absent structure.**
A position the champion plays but has no games in during the window renders "Not enough
data": no win/loss block, so nothing to fingerprint a state change with. Both the capture
loop and the parser used the presence of numbers to decide the position existed, so a 24h
scan quietly lost one position each for five champions.

**Rule: read structure from something other than the data itself.** The game states the
positions twice more, and the capture already saves both: the icon row under the champion
name has one icon per playable position, and the selector strip draws them lit. Neither
needs a re-capture, and each vetoes the other when a frame is caught mid-repaint.

**7. Capture raw, parse separately.**
The single most valuable structural decision. `esmo_capture.py` saves raw XML;
`parse_esmo.py` converts it offline. Every parsing bug since has been fixed in seconds by
re-running the parser, instead of repeating a 3-hour walk. Several datasets were fully
repaired after the fact this way.

---

## 5. From exploration to a capture script

The shape that works, in `esmo_capture.py`:

```
detect a working scroll gesture (verify it moves content)
walk the list:
    for each item:
        open it
        for each tab / sub-state:
            wait for load, verifying expected content is present
            scroll to the end, saving every distinct dump
        go back, re-establish list position
        write progress to a manifest after every item
```

Details worth copying:

- **Save progress after every item** so `Ctrl+C` costs one item, not the run
- **Resume by checking what's on disk covers what this run wants** — not just "seen this
  name before", or a partial pass masks a full one
- **Cache the list layout.** The grid has no names, so identifying a cell means opening it
  (~7s). Storing a cell→name map turns an 8-minute resume into seconds. Validate it on
  first mismatch and fall back to opening everything if the list shifted.
- **Flag problems per item** in the manifest and print a ready-made re-run command
- **Deduplicate identical data** — it usually means an ignored tap

For parsing, the dump gives `(text, x1, y1, x2, y2, class, clickable)` per node. Two
patterns cover most layouts:

- **Label/value rows**: nodes sharing a `y1`, label on the left, value right-aligned. Scope
  keys by their section header, or duplicate labels collide — `ARMOR/Armor` = `25 (+4/lvl)`
  and `PENETRATION/Armor` = `0%` are different values with the same label.
- **Composite nodes**: one node holds several fields separated by newlines, e.g.
  `'Automata\nvs\nGambler\n67.6%\nWR\n34\nG'`. Split and take positionally, with a sanity
  check on each field — the placeholder emitted `vs 1 — 52.0%` and a bare number is never a
  champion name.

---

## 6. Reference — what's already mapped (1920×1080)

| Element | Coordinates |
|---|---|
| Champion grid | origin x=849, y=494; pitch 106.9 × 106.8; 10 cols; 7 rows + 2 = 72 |
| Grid rows, scrolled to top | y = 494, 601, 708, 815, 921 |
| Grid rows, scrolled to bottom | y = 276, 383, 490, 597, 704, 810, 917, 1024 |
| Detail tabs | Overview (971,324) · Meta (1331,324) · Stats (1691,324) |
| Champion name node | y 144–192, x1 > 900 |
| Portrait | (815,144)–(935,264) |
| Position selector | strip (1061,540)–(1601,612); icons x = 1115/1223/1331/1439/1547, y = 576 |
| Position order | Top, Jungle, Mid, Bot, Support |
| Date range button | (931,408) · More (1113,408) |
| Date picker options | Any / Last 24h / Last 7 days / Last 28 days — matched by text |
| Back | (833,78) or `keyevent 4` |

Scripts: `esmo_explore.py` (map any page) · `esmo_capture.py` (walk champions) ·
`parse_esmo.py` (offline parse) · `esmo_probe.py` (original recon).

Useful flags: `--range 7d|28d|none` · `--limit N` · `--resume` · `--redo NAMES` ·
`--phase top|bottom` · `--no-meta` · `--probe-roles` · `--probe-filter` · `--pull-apk`.

---

## 7. What can't be extracted

Only genuine pixels:

- **Champion art and icons** — cropped from screenshots, or pull the APK (`--pull-apk`);
  Flutter keeps bundled images in `assets/flutter_assets/`
- **Charts** — the early-game advantages graph exposes axis labels
  (`0m…20m`, `Gold`, `CS`, `XP`) but no plotted values. Chart *data* is not in the tree.

Everything else on every page examined so far has been text.

---

## 8. Pages worth exploring next

Visible in the app and unmapped:

| Page | Likely value | Expected difficulty |
|---|---|---|
| Playbook → **Items** | item stats, costs, build paths | Low — probably a labelled list |
| Playbook → **Draft** | draft rules, pick/ban structure | Low–medium |
| Playbook → **Strategies** | strategy definitions | Low–medium |
| **Competitions** / **Schedule** | leagues, fixtures, standings | Medium — likely paginated |
| **Skins** | cosmetic catalogue | Low |
| Team / player pages | player attributes, contracts, form | Medium — one page per player |
| **Matchmaking** | queue and rating structure | Unknown |

Start with **Items** — a labelled list avoids the identify-by-opening problem that made the
champion grid expensive, and item data pairs naturally with the champion data you already
have.

The unexplored **`More`** button on the Meta tab (1113,408) may hold further filters —
region, league, rank. If it does, every existing metric gains a dimension.

---

## 9. Before extending this

The ESMO devs have not been asked about automated extraction. A Discord message was drafted
but not sent. Worth doing before scaling this up — and if they offer a data export or an
endpoint, that replaces this entire approach with something that stays correct across
patches.
