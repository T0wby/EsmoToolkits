#!/usr/bin/env python3
"""
ESMO champion capture - step 2 of the pipeline.

Walks every champion in the Playbook > Heroes grid and saves the raw accessibility
tree for all three tabs (Overview / Meta / Stats), plus a cropped portrait.

It saves RAW data. Parsing into final JSON is done by parse_esmo.py, so a parsing
mistake never costs you another capture run.

Before running
--------------
  * BlueStacks running, ESMO open, on Playbook -> Heroes (the champion grid)
  * ADB enabled in BlueStacks
  * Optional but recommended: pip install Pillow   (for portrait cropping)

Usage
-----
  python scripts/esmo_capture.py
  python scripts/esmo_capture.py --port 5555
  python scripts/esmo_capture.py --resume            # skip champions already captured
  python scripts/esmo_capture.py --limit 3           # test run on the first 3 champions
  python scripts/esmo_capture.py --pull-apk          # also pull the APKs for full-res art

Output: esmo_capture/ with raw/<Champion>/*.xml, portraits/<Champion>.png,
        captured.json, and esmo_capture.zip at the end.

Expect roughly 25-35 minutes for the full roster. It prints progress as it goes and
can be stopped with Ctrl+C and resumed with --resume.
"""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

# ---------------------------------------------------------------- geometry
# Measured from a 1920x1080 BlueStacks instance (density 240).
EXPECTED_W, EXPECTED_H = 1920, 1080

GRID_X0, GRID_DX, GRID_COLS = 849, 106.9, 10
GRID_ROWS_TOP = [494, 601, 708, 815, 921]                 # grid scrolled to top
GRID_ROWS_BOTTOM = [276, 383, 490, 597, 704, 810, 917, 1024]  # grid scrolled to bottom

# The Meta tab has an unlabelled 5-icon position selector. Champions can be played
# in up to 3 positions and each has its own stats, so all five are probed.
# Order matches the role filter row on the Heroes grid.
ROLE_ICONS = [(1115, 576), (1223, 576), (1331, 576), (1439, 576), (1547, 576)]
ROLE_NAMES = ["Top", "Jungle", "Mid", "Bot", "Support"]
ROLE_STRIP_BOX = (1061, 540, 1601, 612)

DATE_BTN = (931, 408)       # range selector on the Meta tab, e.g. "Jul 17 - Aug 14"
MORE_BTN = (1113, 408)      # "More" - additional filters, contents unexplored

# The range picker is a modal listing these options. It does NOT persist between
# champions - it reverts to "Last 28 days" every time a champion is opened - so it
# has to be re-applied per champion.
DATE_RANGE_LABELS = {
    "24h": "Last 24h",
    "7d": "Last 7 days",
    "28d": "Last 28 days",
    "any": "Any",              # subscription required for full history
}
DIALOG_DISMISS = (1700, 980)   # outside the dialog box, hits the Dismiss overlay
DIALOG_CLOSE_X = (1338, 234)

TAB_OVERVIEW = (971, 324)
TAB_META = (1331, 324)
TAB_STATS = (1691, 324)
BACK_BTN = (833, 78)

GRID_SWIPE_UP = (1330, 900, 1330, 500)
GRID_SWIPE_DOWN = (1330, 500, 1330, 900)

# In-page scrolling. A 350ms fling turned out not to move Flutter's scroll view at all
# on BlueStacks, so several gestures are tried and the first one that actually moves
# content is used for the rest of the run.
SCROLL_METHODS = {
    "swipe_800": {
        "down": ("input", "swipe", "1330", "950", "1330", "450", "800"),
        "up":   ("input", "swipe", "1330", "450", "1330", "950", "800"),
    },
    "swipe_1500": {
        "down": ("input", "swipe", "1330", "950", "1330", "450", "1500"),
        "up":   ("input", "swipe", "1330", "450", "1330", "950", "1500"),
    },
    "swipe_short": {
        "down": ("input", "swipe", "1330", "900", "1330", "620", "900"),
        "up":   ("input", "swipe", "1330", "620", "1330", "900", "900"),
    },
    "swipe_leftcol": {
        "down": ("input", "swipe", "900", "950", "900", "450", "800"),
        "up":   ("input", "swipe", "900", "450", "900", "950", "800"),
    },
    "swipe_300": {
        "down": ("input", "swipe", "1330", "950", "1330", "450", "300"),
        "up":   ("input", "swipe", "1330", "450", "1330", "950", "300"),
    },
    "touchscreen_800": {
        "down": ("input", "touchscreen", "swipe", "1330", "950", "1330", "450", "800"),
        "up":   ("input", "touchscreen", "swipe", "1330", "450", "1330", "950", "800"),
    },
    "pagekeys": {
        "down": ("input", "keyevent", "93"),
        "up":   ("input", "keyevent", "92"),
    },
}
SCROLL_ORDER = ["swipe_800", "swipe_1500", "swipe_short", "swipe_leftcol",
                "swipe_300", "touchscreen_800", "pagekeys"]

PORTRAIT_BOX = (815, 144, 935, 264)           # champion art on the detail header

MAX_SCROLLS = 10
PACKAGE = "gg.esmo"

# Output lands in the directory you run from, not beside the script: an installed
# copy lives in site-packages, which is no place for capture data.
OUT_ROOT = pathlib.Path.cwd()
OUT = OUT_ROOT / "esmo_capture"

CANDIDATE_PORTS = [5555, 5556, 5557, 5565, 5575, 5585,
                   62001, 62025, 62026, 21503, 21513]


# ---------------------------------------------------------------- adb plumbing
class Adb:
    def __init__(self, adb_path, serial=None):
        self.adb, self.serial = adb_path, serial

    def run(self, *args, binary=False, timeout=60):
        cmd = [self.adb] + (["-s", self.serial] if self.serial else []) + list(args)
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if binary:
            return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")
        return (p.returncode, p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))

    def shell(self, *a, **kw):
        return self.run("shell", *a, **kw)

    def tap(self, x, y, wait=1.4):
        self.shell("input", "tap", str(int(x)), str(int(y)))
        time.sleep(wait)

    def swipe(self, box, ms=350, wait=0.9):
        x1, y1, x2, y2 = box
        self.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))
        time.sleep(wait)

    def back(self, wait=1.4):
        self.shell("input", "keyevent", "4")
        time.sleep(wait)


def find_adb(explicit=None):
    if explicit:
        if pathlib.Path(explicit).exists():
            return explicit
        sys.exit(f"adb not found at: {explicit}")
    found = shutil.which("adb")
    if found:
        return found
    for g in [r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
              r"C:\Program Files\BlueStacks_nxt\adb.exe",
              r"C:\platform-tools\adb.exe",
              "/usr/local/bin/adb", "/usr/bin/adb"]:
        if pathlib.Path(g).exists():
            return g
    sys.exit("Could not find adb. Pass --adb <path to adb.exe>.")


def connect(adb, port=None):
    for p in ([port] if port else CANDIDATE_PORTS):
        adb.run("connect", f"127.0.0.1:{p}", timeout=15)
    _, out, _ = adb.run("devices")
    return [ln.split("\t")[0].strip() for ln in out.splitlines()[1:]
            if "\t" in ln and ln.split("\t")[1].strip() == "device"]


# ---------------------------------------------------------------- ui dumping
def dump_xml(adb):
    """Return the raw uiautomator XML for the current screen."""
    for attempt in range(3):
        rc, out, err = adb.shell("uiautomator", "dump", "/sdcard/esmo_ui.xml", timeout=60)
        blob = (out or "") + (err or "")
        if "ERROR" in blob.upper():
            time.sleep(1.0)
            continue
        rc, xml, err = adb.shell("cat", "/sdcard/esmo_ui.xml", timeout=60)
        # adb writes its own chatter to STDOUT, not stderr ("* daemon started
        # successfully *", and the version-mismatch banner when a second adb owns
        # the server), so it lands in front of the XML. Slice from the declaration
        # instead of demanding the payload start there - requiring startswith made
        # every dump return None and the caller report "no text nodes", which reads
        # as "this screen has no accessibility tree" and is a lie.
        start = xml.find("<?xml") if xml else -1
        if start >= 0:
            return xml[start:]
        time.sleep(1.0)
    return None


def nodes_of(xml):
    """[(desc, x1, y1, x2, y2, cls, clickable)] for every node carrying text."""
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for n in root.iter("node"):
        desc = (n.get("content-desc") or "").strip() or (n.get("text") or "").strip()
        if not desc:
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        out.append((desc, x1, y1, x2, y2,
                    (n.get("class") or "").split(".")[-1],
                    n.get("clickable") == "true"))
    return out


def is_detail_page(nodes):
    return any(d.startswith("Overview\n") for d, *_ in nodes)


def is_grid_page(nodes):
    return any(d.startswith("Heroes\n") for d, *_ in nodes)


def champion_name(nodes):
    """Header title node sits at y 144..192 on the detail page."""
    for d, x1, y1, x2, y2, cls, ck in nodes:
        if 130 <= y1 <= 160 and 178 <= y2 <= 212 and x1 > 900 and "\n" not in d:
            return d
    return None


RE_WINBLOCK = re.compile(r"\d+\.\d+%\n\d+W \d+L")


def signature(xml):
    """Content fingerprint including positions, so a scroll of any size shows up."""
    return frozenset((d, y1) for d, x1, y1, x2, y2, cls, ck in nodes_of(xml))


def wait_until_stable(adb, require=None, min_stable=2, interval=0.5, timeout=20.0,
                      min_wait=0.0):
    """Poll the UI until it stops changing.

    The Meta tab paints cached data first and swaps in server aggregates a moment
    later, so dumping immediately captures the wrong numbers. This waits for the
    content to settle, and optionally for `require` to appear in it first.

    Returns (xml, settled).
    """
    t0 = time.time()
    last, stable, best = None, 0, None
    while time.time() - t0 < timeout:
        xml = dump_xml(adb)
        if xml:
            best = xml
            sig = signature(xml)
            satisfied = (require is None) or bool(require.search(
                "\n".join(d for d, *_ in nodes_of(xml))))
            if sig == last and satisfied:
                stable += 1
                if stable >= min_stable and (time.time() - t0) >= min_wait:
                    return xml, True
            else:
                stable = 0
            last = sig
        time.sleep(interval)
    return best, False


def do_scroll(adb, method, direction, wait=1.0):
    adb.shell(*SCROLL_METHODS[method][direction])
    time.sleep(wait)


def scroll_to_top(adb, method, times=6):
    for _ in range(times):
        do_scroll(adb, method, "up", wait=0.5)


def detect_scroll_method(adb):
    """Find a gesture that actually moves this Flutter scroll view."""
    print("\ndetecting a working scroll gesture (one-off, ~1 min)...")
    for name in SCROLL_ORDER:
        scroll_to_top(adb, name, times=5)
        before = signature(dump_xml(adb))
        do_scroll(adb, name, "down", wait=1.2)
        after = signature(dump_xml(adb))
        moved = before != after
        print(f"  {name:16} {'MOVED' if moved else 'no effect'}")
        if moved:
            scroll_to_top(adb, name)
            print(f"  -> using '{name}'\n")
            return name
    print("  !! no gesture moved the content\n")
    return None


def collect_tab(adb, outdir, tab_name, method, require=None, settle_timeout=20.0):
    """Scroll a tab top to bottom, saving every distinct dump.

    Returns (filenames, complete) - complete is False when the content was taller
    than one screen but could not be scrolled, or when it never settled.
    """
    if method:
        scroll_to_top(adb, method)

    saved, seen, idx, stalled = [], [], 0, False

    first, settled = wait_until_stable(adb, require=require, timeout=settle_timeout)
    if not settled:
        stalled = True

    for i in range(MAX_SCROLLS):
        xml = first if i == 0 else wait_until_stable(adb, timeout=8.0)[0]
        if not xml:
            break
        sig = signature(xml)
        if sig in seen:
            break
        seen.append(sig)
        path = outdir / f"{tab_name}_{idx:02d}.xml"
        path.write_text(xml, encoding="utf-8")
        saved.append(path.name)
        idx += 1

        if not method:
            stalled = True
            break

        do_scroll(adb, method, "down")

    # If only one screen was captured, confirm that is genuinely all there is
    # rather than a scroll that silently did nothing.
    if len(saved) == 1 and method:
        do_scroll(adb, method, "down", wait=1.2)
        if signature(dump_xml(adb)) != seen[0]:
            stalled = True   # content DID move - the first loop exited too early

    return saved, not stalled


def crop(png_bytes, box, dest):
    try:
        from PIL import Image
        import io
    except ImportError:
        return False
    try:
        Image.open(io.BytesIO(png_bytes)).crop(box).save(dest)
        return True
    except Exception:
        return False


ROLE_ENABLED_MAX = 180      # playable icons peak at 227, greyed-out ones at ~125


def role_states(png_bytes):
    """Read the position selector from pixels.

    The five icons carry no text, but they are drawn differently depending on
    state: a position the champion can play peaks around 227 luminance, one it
    cannot around 125, and the currently selected one sits on a lit background
    that roughly doubles the cell's mean brightness.

    Returns [{'enabled': bool, 'mean': float}] or None if it can't be read.
    """
    try:
        from PIL import Image
        import io
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        x0, y0, x1, y1 = ROLE_STRIP_BOX
        w = (x1 - x0) / len(ROLE_ICONS)
        out = []
        for i in range(len(ROLE_ICONS)):
            px = list(im.crop((int(x0 + i * w), y0, int(x0 + (i + 1) * w), y1))
                      .getdata())
            out.append({"enabled": max(px) >= ROLE_ENABLED_MAX,
                        "mean": sum(px) / len(px)})
        return out if any(s["enabled"] for s in out) else None
    except Exception:
        return None


def winblock_key(xml):
    """The win/loss summary string, used as a fingerprint for 'which position'."""
    for d, *_ in nodes_of(xml):
        if RE_WINBLOCK.match(d):
            return d.split("\n")[1]      # e.g. "623W 714L · 1337 games"
    return None


def screencap(adb):
    rc, data, err = adb.run("exec-out", "screencap", "-p", binary=True, timeout=60)
    if rc != 0 or not data:
        return None
    if not data.startswith(b"\x89PNG"):
        data = data.replace(b"\r\n", b"\n")
    return data


# ---------------------------------------------------------------- capture loop
def has_meta(rec):
    return any(k.startswith("meta") and rec["files"][k] for k in rec.get("files", {}))


def capture_champion(adb, name, raw_root, portrait_root, have_pil, method,
                     do_roles=True, do_meta=True, date_range=None,
                     expected_range=None):
    cdir = raw_root / re.sub(r"[^A-Za-z0-9 _-]+", "", name).strip()
    cdir.mkdir(parents=True, exist_ok=True)

    files, incomplete = {}, []
    range_label = None

    # Clear anything left by an earlier run. Without this, a position captured
    # last time but not this time keeps its old XML on disk and gets parsed as
    # real - silently mixing two capture dates in one dataset.
    for f in cdir.glob("overview_*.xml"):
        f.unlink()
    for f in cdir.glob("stats_*.xml"):
        f.unlink()
    if do_meta:
        for f in cdir.glob("meta_*.xml"):
            f.unlink()

    files["overview"], ok = collect_tab(adb, cdir, "overview", method)
    if not ok:
        incomplete.append("overview")

    png = screencap(adb)
    portrait_ok = False
    if png and have_pil:
        portrait_ok = crop(png, PORTRAIT_BOX, portrait_root / f"{cdir.name}.png")

    roles_found = []
    if not do_meta:
        pass                     # --no-meta: straight from Overview to Stats
    elif do_roles:
        # Meta loads asynchronously - insist on the win/loss block appearing and
        # the whole tab settling before anything is written.
        adb.tap(*TAB_META, wait=2.0)
        # The first paint of the Meta tab shows a cached placeholder; wait it out
        # before reading anything. Position switches afterwards are immediate.
        wait_until_stable(adb, require=RE_WINBLOCK, timeout=30.0, min_wait=5.0)

        # Apply the date range BEFORE reading anything - it refetches the numbers
        # and repaints the position icons.
        if date_range:
            applied = set_date_range(adb, date_range, expected_range)
            if applied:
                range_label = applied
            else:
                incomplete.append("date-range")

        if method:
            scroll_to_top(adb, method)

        strip = screencap(adb)
        states = role_states(strip) if strip else None
        if strip and have_pil:
            crop(strip, ROLE_STRIP_BOX,
                 portrait_root / f"_rolestrip_{cdir.name}.png")

        if states:
            # Only the playable positions need visiting, and the one already
            # selected needs no tap at all.
            enabled = [i for i, s in enumerate(states) if s["enabled"]]
            selected = max(enabled, key=lambda i: states[i]["mean"])
            order = [selected] + [i for i in enabled if i != selected]
        else:
            # Could not read the selector - usually a stale/black frame because
            # the emulator window is minimised. Fall back to tapping all five,
            # but flag it: that path can mislabel positions.
            incomplete.append("roles-unverified")
            enabled, selected, order = list(range(len(ROLE_ICONS))), None, \
                list(range(len(ROLE_ICONS)))

        seen_blocks = {}
        for idx in order:
            role = ROLE_NAMES[idx]
            if idx != selected:
                if method:
                    scroll_to_top(adb, method)
                adb.tap(*ROLE_ICONS[idx], wait=1.0)
                xml, _ = wait_until_stable(adb, require=RE_WINBLOCK,
                                           timeout=16.0, min_wait=1.5)
                key = winblock_key(xml) if xml else None
                if not key or key in seen_blocks:
                    continue        # tap ignored - position not actually played
                seen_blocks[key] = role
            else:
                # Must settle here too. A bare dump can miss the win block, and
                # then the seen-set stays empty and the NEXT position - whose tap
                # did nothing - gets recorded as real.
                xml, _ = wait_until_stable(adb, require=RE_WINBLOCK, timeout=16.0)
                key = winblock_key(xml) if xml else None
                if not key:
                    continue        # no data in this window for this position
                seen_blocks[key] = role

            names, ok = collect_tab(adb, cdir, f"meta_{role}", method,
                                    require=RE_WINBLOCK, settle_timeout=30.0)

            # Belt and braces: whatever the pre-check said, if what we actually
            # collected duplicates a position already recorded, this position is
            # not playable - the tap was ignored and the old data stayed up.
            collected = None
            for fn in names:
                collected = winblock_key((cdir / fn).read_text(encoding="utf-8"))
                if collected:
                    break
            if collected and collected in seen_blocks and seen_blocks[collected] != role:
                for fn in names:
                    (cdir / fn).unlink(missing_ok=True)
                continue
            if collected:
                seen_blocks[collected] = role

            files[f"meta_{role}"] = names
            roles_found.append(role)
            if not ok:
                incomplete.append(f"meta:{role}")

        if not roles_found:
            incomplete.append("meta")
    else:
        adb.tap(*TAB_META, wait=2.0)
        if date_range:
            applied = set_date_range(adb, date_range, expected_range)
            if applied:
                range_label = applied
            else:
                incomplete.append("date-range")
        files["meta"], ok = collect_tab(adb, cdir, "meta", method,
                                        require=RE_WINBLOCK, settle_timeout=30.0)
        if not ok:
            incomplete.append("meta")

    adb.tap(*TAB_STATS, wait=1.6)
    files["stats"], ok = collect_tab(adb, cdir, "stats", method)
    if not ok:
        incomplete.append("stats")

    return {"name": name, "dir": cdir.name, "files": files, "roles": roles_found,
            "portrait": portrait_ok, "incomplete": incomplete,
            "range": range_label}


def probe_roles(adb, method):
    """Measure how the position selector behaves, on one champion.

    Open a champion's Meta tab (ideally one played in several positions) and run
    with --probe-roles. Taps each of the five icons and records a timed series of
    dumps, so the placeholder->live swap timing and the look of an unplayed
    position can both be measured instead of guessed.
    """
    out = OUT_ROOT / "esmo_roleprobe"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    nodes = nodes_of(dump_xml(adb))
    champ = champion_name(nodes) or "unknown"
    print(f"\nprobing position selector on: {champ}")
    if not any(d.startswith("Meta\n") for d, *_ in nodes):
        print("!! Open a champion and switch to the Meta tab first.")
        return

    log = []
    for idx, (rx, ry) in enumerate(ROLE_ICONS):
        role = ROLE_NAMES[idx]
        if method:
            scroll_to_top(adb, method)
        print(f"\n  slot {idx + 1} ({role}) - tapping, sampling 12s")
        adb.shell("input", "tap", str(rx), str(ry))
        t0 = time.time()
        for i in range(12):
            xml = dump_xml(adb)
            if xml:
                (out / f"{idx}_{role}_{i:02d}.xml").write_text(xml, encoding="utf-8")
                key = winblock_key(xml)
                dt = time.time() - t0
                line = f"    t={dt:5.1f}s  {key or '(no win block)'}"
                print(line)
                log.append(f"{role}\t{dt:.1f}\t{key or ''}")
            time.sleep(0.6)

    png = screencap(adb)
    if png:
        (out / "meta_screen.png").write_bytes(png)
        states = role_states(png)
        if states:
            print("\n  position selector read from pixels:")
            for i, s in enumerate(states):
                print(f"    {ROLE_NAMES[i]:8} {'playable' if s['enabled'] else 'greyed out':11}"
                      f" (mean {s['mean']:.1f})")
    (out / "log.txt").write_text(f"champion: {champ}\n" + "\n".join(log),
                                 encoding="utf-8")

    zpath = OUT_ROOT / "esmo_roleprobe.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.iterdir()):
            zf.write(f, f.name)
    print(f"\nwrote {zpath} - send it to Claude")


def date_button_label(nodes):
    """Current range shown on the Meta tab, e.g. 'Aug 7 - Aug 14'."""
    for d, x1, y1, x2, y2, cls, ck in nodes:
        if 370 <= y1 <= 400 and x1 < 1045 and cls == "Button":
            return d
    return None


def dialog_open(nodes):
    return any(d == "Date range" for d, *_ in nodes)


def dismiss_dialog(adb, tries=3):
    """Close the range modal if it is open. A modal left open hides the whole
    Meta tab from the accessibility tree, which then looks like 'not loaded yet'
    and burns the retry budget."""
    for _ in range(tries):
        if not dialog_open(nodes_of(dump_xml(adb))):
            return True
        adb.tap(*DIALOG_CLOSE_X, wait=1.2)
        if not dialog_open(nodes_of(dump_xml(adb))):
            return True
        adb.tap(*DIALOG_DISMISS, wait=1.2)
    return not dialog_open(nodes_of(dump_xml(adb)))


def wait_for_dialog(adb, timeout=12.0):
    """Poll until the range modal is actually on screen."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        nodes = nodes_of(dump_xml(adb))
        if dialog_open(nodes):
            return nodes
        time.sleep(0.5)
    return None


def set_date_range(adb, key, expected=None, attempts=3):
    """Open the range picker and select `key`. Returns the resulting label.

    The range button is inert until the Meta tab has finished loading - tapping it
    early does nothing and the champion is then captured in the wrong window. So
    this waits for the win/loss block itself rather than trusting the caller to
    have done it, and retries if the dialog fails to open.

    The picker also resets to 28 days for every champion, so this runs per champion.
    Options are matched by their text, not by fixed coordinates.
    """
    want = DATE_RANGE_LABELS[key]

    for attempt in range(attempts):
        dismiss_dialog(adb)              # clear a modal stuck open by a prior try

        # The tab must be loaded before the range button responds to a tap.
        loaded, ok = wait_until_stable(adb, require=RE_WINBLOCK, timeout=30.0,
                                       min_wait=1.0)
        if not ok:
            continue

        before = date_button_label(nodes_of(loaded))
        if expected and before == expected:
            return expected              # already correct, nothing to do

        adb.tap(*DATE_BTN, wait=1.2)
        nodes = wait_for_dialog(adb, timeout=12.0)
        if nodes is None:
            continue                     # button was still inert - try again

        target = next((n for n in nodes
                       if n[0].split("\n")[0] == want and n[6]), None)
        if target is None:
            dismiss_dialog(adb)
            return None                  # option absent; retrying won't help

        _, x1, y1, x2, y2, _, _ = target
        adb.tap((x1 + x2) // 2, (y1 + y2) // 2, wait=1.5)
        dismiss_dialog(adb)              # selecting doesn't always close it

        # Changing the range refetches, so let the numbers land before reading.
        wait_until_stable(adb, require=RE_WINBLOCK, timeout=30.0, min_wait=3.0)
        label = date_button_label(nodes_of(dump_xml(adb)))

        # Accepting any label would let a silently-ignored tap pass as success.
        if expected:
            if label == expected:
                return label
        elif label and label != before:
            return label

    return None


def _snap(adb, out, tag, note=""):
    """Save a dump + screenshot and print every text node with its coordinates."""
    xml = dump_xml(adb)
    if xml:
        (out / f"{tag}.xml").write_text(xml, encoding="utf-8")
    png = screencap(adb)
    if png:
        (out / f"{tag}.png").write_bytes(png)
    nodes = nodes_of(xml)
    print(f"\n  --- {tag} {note}")
    for d, x1, y1, x2, y2, cls, ck in sorted(nodes, key=lambda n: (n[2], n[1])):
        if is_chrome_desc(d):
            continue
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        flag = "*" if ck else " "
        print(f"   {flag} tap({cx:4d},{cy:4d}) {cls:11} {d[:70]!r}")
    return nodes


def is_chrome_desc(d):
    return d in ("Back", "Playbook") or re.match(
        r"^(Overview|Meta|Stats|Heroes|Draft|Strategies)\nTab \d of \d$", d)


def probe_filter(adb, method):
    """Work out how the date-range filter behaves.

    Open a champion's Meta tab and run with --probe-filter. Records what the range
    picker offers, lets you select an option, then checks whether the choice sticks
    when you open a different champion - which decides whether the capture sets the
    filter once per run or once per champion.
    """
    out = OUT_ROOT / "esmo_filterprobe"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    nodes = nodes_of(dump_xml(adb))
    if not any(d.startswith("Meta\n") for d, *_ in nodes):
        print("!! Open a champion and switch to the Meta tab first.")
        return
    champ = champion_name(nodes) or "unknown"
    print(f"probing filters on: {champ}")

    if method:
        scroll_to_top(adb, method)
    _snap(adb, out, "00_before", "(current state)")

    print("\ntapping the date-range button...")
    adb.tap(*DATE_BTN, wait=1.5)
    wait_until_stable(adb, timeout=10.0)
    _snap(adb, out, "01_date_picker", "(what the range button opens)")

    print("\n" + "=" * 66)
    print("Pick the WEEKLY option using its tap(x,y) from the list above.")
    print("  t <x> <y>   tap and re-capture")
    print("  <Enter>     re-capture without tapping")
    print("  b           press Back")
    print("  q           done - then I check whether the choice persists")
    print("=" * 66)

    i = 2
    while True:
        try:
            cmd = input(f"  [{i:02d}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if cmd.lower() in ("q", "quit"):
            break
        if cmd.lower().startswith("t "):
            p = cmd.split()
            if len(p) == 3:
                adb.tap(int(p[1]), int(p[2]), wait=1.5)
                wait_until_stable(adb, timeout=12.0)
            else:
                print("   usage: t <x> <y>")
                continue
        elif cmd.lower() == "b":
            adb.back()
        _snap(adb, out, f"{i:02d}_step")
        i += 1

    # What does the range button read now?
    if method:
        scroll_to_top(adb, method)
    after = nodes_of(dump_xml(adb))
    chosen = next((d for d, x1, y1, *_ in after
                   if 370 <= y1 <= 400 and x1 < 1035), None)
    print(f"\n  range button now reads: {chosen!r}")

    # Does it stick for a different champion?
    print("\nchecking whether the filter persists to another champion...")
    adb.back()
    goto_grid(adb)
    scroll_grid(adb, False, method)
    adb.tap(GRID_X0 + 3 * GRID_DX, GRID_ROWS_TOP[1], wait=2.0)
    n2 = nodes_of(dump_xml(adb))
    if is_detail_page(n2):
        other = champion_name(n2)
        adb.tap(*TAB_META, wait=2.0)
        wait_until_stable(adb, require=RE_WINBLOCK, timeout=30.0, min_wait=5.0)
        if method:
            scroll_to_top(adb, method)
        n3 = _snap(adb, out, "90_other_champion", f"({other})")
        other_range = next((d for d, x1, y1, *_ in n3
                            if 370 <= y1 <= 400 and x1 < 1035), None)
        print(f"\n  {other} range button reads: {other_range!r}")
        verdict = ("PERSISTS - set the filter once per run"
                   if other_range == chosen else
                   "RESETS - filter must be set for every champion")
        print(f"  => {verdict}")
        (out / "verdict.txt").write_text(
            f"champion: {champ}\nchosen: {chosen}\n"
            f"other: {other} -> {other_range}\nverdict: {verdict}\n",
            encoding="utf-8")

    zpath = OUT_ROOT / "esmo_filterprobe.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.iterdir()):
            zf.write(f, f.name)
    print(f"\nwrote {zpath} - send it to Claude")


def goto_grid(adb, tries=4):
    for _ in range(tries):
        nodes = nodes_of(dump_xml(adb))
        if is_grid_page(nodes):
            return True
        adb.back()
    return False


def grid_at_top(adb):
    """The Meta/Items buttons sit above the grid and scroll away with it."""
    return any(d in ("Meta", "Items") for d, *_ in nodes_of(dump_xml(adb)))


def scroll_grid(adb, to_bottom, method=None, max_steps=10):
    """Move the champion grid to one end, verifying it actually moved.

    The earlier version fired swipe gestures, which do nothing here - so the grid
    never left the top and the last two rows were unreachable.
    """
    want_top = not to_bottom
    direction = "down" if to_bottom else "up"
    methods = ([method] if method else []) + [m for m in SCROLL_ORDER if m != method]

    for m in methods:
        for _ in range(max_steps):
            if grid_at_top(adb) == want_top:
                return True
            do_scroll(adb, m, direction, wait=0.6)
        if grid_at_top(adb) == want_top:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb")
    ap.add_argument("--port", type=int)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pull-apk", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--scroll-method", choices=list(SCROLL_METHODS),
                    help="skip gesture auto-detection and force one")
    ap.add_argument("--no-roles", action="store_true",
                    help="capture only the default position (faster, less complete)")
    ap.add_argument("--range", choices=list(DATE_RANGE_LABELS) + ["none"],
                    dest="date_range", default="7d",
                    help="Meta date range, re-applied per champion. Default: 7d "
                         "(weekly). Use 'none' to leave the app's own 28-day "
                         "window untouched.")
    ap.add_argument("--probe-filter", action="store_true",
                    help="explore the date-range filter on the currently open champion")
    ap.add_argument("--probe-roles", action="store_true",
                    help="measure the position selector on the currently open champion")
    ap.add_argument("--redo", metavar="NAMES",
                    help="comma-separated champion names to re-capture; their "
                         "existing data is discarded first. Implies --resume, "
                         "so the rest of the roster is skipped.")
    ap.add_argument("--phase", choices=["top", "bottom", "both"], default="both",
                    help="which grid scroll position to walk; 'bottom' reaches the "
                         "last rows without re-visiting the first ones")
    ap.add_argument("--no-meta", action="store_true",
                    help="skip the Meta tab entirely - capture only overview, "
                         "stats and portraits (much faster)")
    args = ap.parse_args()

    adb_path = find_adb(args.adb)
    probe = Adb(adb_path)
    devices = connect(probe, args.port)
    if not devices:
        sys.exit("No adb device. Is BlueStacks running with ADB enabled?")
    adb = Adb(adb_path, devices[0])
    print(f"adb: {adb_path}\ndevice: {devices[0]}")

    _, size, _ = adb.shell("wm", "size")
    m = re.search(r"(\d+)x(\d+)", size or "")
    if m and (int(m.group(1)), int(m.group(2))) != (EXPECTED_W, EXPECTED_H):
        print(f"\n!! Screen is {m.group(0)}, coordinates were measured on "
              f"{EXPECTED_W}x{EXPECTED_H}.")
        print("   Set BlueStacks to 1920x1080 or the taps will land wrong.")
        if input("   Continue anyway? [y/N] ").strip().lower() != "y":
            return

    if args.probe_roles:
        probe_roles(adb, args.scroll_method or "pagekeys")
        return

    if args.probe_filter:
        probe_filter(adb, args.scroll_method or "pagekeys")
        return

    try:
        from PIL import Image  # noqa: F401
        have_pil = True
    except ImportError:
        have_pil = False
        print("\n(Pillow not installed - portraits will be skipped.")
        print(" Run 'pip install Pillow' and re-run with --resume to add them.)")

    OUT.mkdir(exist_ok=True)
    raw_root = OUT / "raw"
    portrait_root = OUT / "portraits"
    raw_root.mkdir(exist_ok=True)
    portrait_root.mkdir(exist_ok=True)

    state_path = OUT / "captured.json"
    captured, cells = {}, {}
    cells_trusted, skipped = True, 0
    # --redo only makes sense against an existing state file: it drops those names
    # from the skip list so they get walked again. Without --resume the skip list
    # is empty, so the run would restart at the first champion AND overwrite
    # captured.json with nothing. Imply it rather than doing either.
    if args.redo and not args.resume:
        args.resume = True
        print("--redo implies --resume")
    if args.resume and state_path.exists():
        _state = json.loads(state_path.read_text())
        captured = {c["name"]: c for c in _state["champions"]}
        cells = dict(_state.get("cells") or {})
        print(f"resuming - {len(captured)} champions already captured")
        if cells:
            print(f"           {len(cells)} grid cells mapped - finished "
                  f"champions will be skipped without opening them")

    # Sweep leftovers from earlier runs across the WHOLE folder, not just the
    # champions this run touches. A champion skipped by --resume keeps its old
    # files otherwise, and those get parsed as current data.
    if captured:
        swept = 0
        for rec in captured.values():
            cdir = raw_root / rec["dir"]
            roles = set(rec.get("roles") or [])
            if not cdir.is_dir() or not roles:
                continue
            for f in cdir.glob("meta_*.xml"):
                m = re.match(r"^meta_([A-Za-z]+)_\d+\.xml$", f.name)
                if m and m.group(1) not in roles:
                    f.unlink()
                    swept += 1
        if swept:
            print(f"  swept {swept} stale file(s) left by an earlier run")

    if args.redo:
        wanted = [n.strip() for n in args.redo.split(",") if n.strip()]
        for name in wanted:
            match = next((k for k in captured if k.lower() == name.lower()), None)
            if match:
                rec = captured.pop(match)
                shutil.rmtree(raw_root / rec["dir"], ignore_errors=True)
                print(f"  re-capturing {match}")
            elif (raw_root / name).exists():
                shutil.rmtree(raw_root / name, ignore_errors=True)
                print(f"  re-capturing {name} (had no state entry)")
            else:
                print(f"  {name}: nothing stored - already queued for capture")
        state_path.write_text(json.dumps(
            {"champions": list(captured.values()), "cells": cells},
            indent=2), encoding="utf-8")

    if args.pull_apk:
        print("\npulling APKs (this may take a minute)...")
        _, paths, _ = adb.shell("pm", "path", PACKAGE)
        apk_dir = OUT / "apk"
        apk_dir.mkdir(exist_ok=True)
        for line in (paths or "").splitlines():
            p = line.replace("package:", "").strip()
            if p:
                adb.run("pull", p, str(apk_dir / pathlib.PurePosixPath(p).name), timeout=600)
                print(f"  {pathlib.PurePosixPath(p).name}")

    # Keep the emulator's screen awake for the duration of an unattended run.
    adb.shell("svc", "power", "stayon", "true")

    if not goto_grid(adb):
        sys.exit("Could not find the champion grid. Open Playbook > Heroes and retry.")

    # A minimised emulator can return blank frames, which would break the pixel-based
    # position detection. Check once up front rather than discovering it 40 minutes in.
    if have_pil:
        shot = screencap(adb)
        if shot:
            from PIL import Image
            import io
            g = Image.open(io.BytesIO(shot)).convert("L")
            if max(g.getdata()) < 60:
                print("\n!! The emulator is returning a blank/dark frame.")
                print("   Restore the BlueStacks window (it may be minimised) and retry.")
                print("   Position detection needs a live frame; it does NOT need focus.")
                return

    print("\nstarting walk - Ctrl+C is safe, use --resume to continue\n")
    t0 = time.time()
    empty_taps = 0
    scroll_method = args.scroll_method
    scroll_probed = scroll_method is not None
    expected_range = None
    if args.date_range == "none":
        args.date_range = None
        print("date range: left as the app default (28 days)\n")
    else:
        print(f"date range: {DATE_RANGE_LABELS[args.date_range]} "
              f"(re-applied per champion - the app does not remember it)\n")

    phases = [(False, GRID_ROWS_TOP), (True, GRID_ROWS_BOTTOM)]
    if args.phase == "top":
        phases = phases[:1]
    elif args.phase == "bottom":
        phases = phases[1:]

    for to_bottom, rows in phases:
        # The bottom view re-shows the rows the top view already covered, so a
        # fresh run re-opens ~50 cells just to discover they are done. One tap
        # confirms the two views line up, and then those rows are skipped.
        if to_bottom and cells.get(f"0:{GRID_ROWS_TOP[0]}:0"):
            expect = cells[f"0:{GRID_ROWS_TOP[0]}:0"]
            if scroll_grid(adb, True, scroll_method):
                adb.tap(GRID_X0, rows[0], wait=1.6)
                probe = nodes_of(dump_xml(adb))
                got = champion_name(probe) if is_detail_page(probe) else None
                adb.back()
                goto_grid(adb)
                if got == expect:
                    overlap = len(GRID_ROWS_TOP)
                    print(f"       bottom view repeats the first {overlap} rows "
                          f"- skipping them (saves ~{overlap * GRID_COLS} taps)")
                    rows = rows[overlap:]
                elif got:
                    print(f"       bottom view starts at {got!r}, not {expect!r} "
                          f"- walking every row")

        if not scroll_grid(adb, to_bottom, scroll_method):
            print(f"!! Could not scroll the grid to the "
                  f"{'bottom' if to_bottom else 'top'}. The last rows of the "
                  f"roster are unreachable - tell Claude rather than trusting "
                  f"this run's coverage.")
            continue
        for ry in rows:
            for col in range(GRID_COLS):
                if args.limit and len(captured) >= args.limit:
                    break
                cx = GRID_X0 + col * GRID_DX
                cell = f"{int(to_bottom)}:{ry}:{col}"

                # The grid shows no names, so normally every cell has to be
                # opened just to find out who is in it - about 7s each, which on
                # a resume means many minutes of apparent hanging. The cell map
                # from previous runs lets known-and-finished champions be skipped
                # without opening them at all.
                known = cells.get(cell)
                if cells_trusted and known and known in captured:
                    rec_done = captured[known]
                    if has_meta(rec_done) or args.no_meta:
                        skipped += 1
                        continue

                adb.tap(cx, ry, wait=1.6)
                nodes = nodes_of(dump_xml(adb))

                if not is_detail_page(nodes):
                    empty_taps += 1
                    if not is_grid_page(nodes):
                        goto_grid(adb)
                        scroll_grid(adb, to_bottom, scroll_method)
                    continue

                name = champion_name(nodes)

                # Validate the cell map the first time it can be checked. If the
                # roster shifted since last run, every later skip would be wrong,
                # so stop trusting it and open every cell instead.
                if name and known and name != known and cells_trusted:
                    print(f"    !! cell map is stale ({cell} was {known}, "
                          f"now {name}) - opening every cell from here")
                    cells_trusted = False
                if name:
                    cells[cell] = name

                if not name:
                    adb.back()
                    goto_grid(adb)
                    scroll_grid(adb, to_bottom, scroll_method)
                    continue

                # Resume skips a champion only if what's on disk already covers
                # what THIS run would capture. So a --no-meta pass followed by a
                # full pass re-visits everything, instead of seeing a name in
                # captured.json and wrongly calling it done.
                done = captured.get(name)
                if done and (has_meta(done) or args.no_meta):
                    skipped += 1
                    # Say something periodically, or a long resume looks like a hang.
                    if skipped % 10 == 0:
                        print(f"       ...skipped {skipped} already-captured "
                              f"(last: {name}, {(time.time()-t0)/60:.1f}m)")
                    adb.back()
                    goto_grid(adb)
                    scroll_grid(adb, to_bottom, scroll_method)
                    continue

                if scroll_method is None and not scroll_probed:
                    adb.tap(*TAB_STATS, wait=1.6)
                    scroll_method = detect_scroll_method(adb)
                    scroll_probed = True
                    if scroll_method is None:
                        print("!! Cannot scroll in-page content. Meta and Stats will be")
                        print("   truncated to the first screen. Stopping so you don't")
                        print("   waste 30 minutes on partial data - tell Claude.")
                        return
                    adb.tap(*TAB_OVERVIEW, wait=1.6)

                rec = capture_champion(adb, name, raw_root, portrait_root,
                                       have_pil, scroll_method,
                                       do_roles=not args.no_roles,
                                       do_meta=not args.no_meta,
                                       date_range=args.date_range,
                                       expected_range=expected_range)
                if rec.get("range") and expected_range is None:
                    expected_range = rec["range"]
                    print(f"       date range applied: {expected_range}")
                elif (args.date_range and expected_range
                        and rec.get("range") not in (None, expected_range)):
                    print(f"    !! {name} range is {rec['range']!r}, "
                          f"expected {expected_range!r}")
                captured[name] = rec
                n = len(captured)
                el = time.time() - t0
                warn = ("  INCOMPLETE:" + ",".join(rec["incomplete"])) if rec["incomplete"] else ""
                roles = "/".join(rec["roles"]) or ("skipped" if args.no_meta
                                                   else "default")
                nmeta = sum(len(v) for k, v in rec["files"].items()
                            if k.startswith("meta"))
                print(f"  [{n:3d}] {name:<18} "
                      f"ov={len(rec['files']['overview'])} "
                      f"meta={nmeta:2d} in {roles:<22} "
                      f"stats={len(rec['files']['stats'])} "
                      f"({el/60:.1f}m){warn}")

                state_path.write_text(json.dumps(
                    {"champions": list(captured.values()), "cells": cells},
                    indent=2), encoding="utf-8")

                adb.back()
                goto_grid(adb)
                scroll_grid(adb, to_bottom, scroll_method)

    print(f"\ndone: {len(captured)} champions in {(time.time()-t0)/60:.1f} min "
          f"({empty_taps} empty cells, {skipped} already-done cells skipped "
          f"without opening)")

    bad = [c["name"] for c in captured.values() if c.get("incomplete")]
    if bad:
        print(f"!! {len(bad)} champion(s) with problems: {', '.join(bad[:12])}")
        dr = [c["name"] for c in captured.values()
              if "date-range" in (c.get("incomplete") or [])]
        if dr:
            print(f"\n   {len(dr)} captured in the WRONG date window. Re-do just those:")
            print(f"   python scripts/esmo_capture.py --resume --redo {','.join(dr)}")
    else:
        print("all tabs scrolled to the end cleanly")

    if not args.no_zip and captured:
        zpath = OUT_ROOT / "esmo_capture.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in OUT.rglob("*"):
                if f.is_file() and f.suffix.lower() != ".apk":
                    zf.write(f, f.relative_to(OUT).as_posix())
        mb = zpath.stat().st_size / 1e6
        print(f"zipped: {zpath} ({mb:.1f} MB)")
        if args.pull_apk:
            print(f"APKs left unzipped in {OUT/'apk'} - they are large; "
                  f"send separately only if asked.")
        print("\nSend esmo_capture.zip back to Claude.")


if __name__ == "__main__":
    main()
