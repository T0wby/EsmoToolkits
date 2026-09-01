#!/usr/bin/env python3
"""
ESMO page explorer - point this at ANY screen in the game.

ESMO is a Flutter app, so every visible string is published to Android's
accessibility tree with exact pixel bounds. This tool reads that tree and prints
each element with a ready-to-use tap coordinate, so you can map an unfamiliar
page without guessing.

Use it to answer, for a new page:
  * what text is on screen, and how is it grouped into nodes?
  * which elements are tappable, and where exactly?
  * how much content is below the fold, and does it scroll?
  * what changes when I tap X?

Then hand the resulting zip to Claude to get a capture script for that page.

Usage
-----
  python scripts/esmo_explore.py                  # interactive
  python scripts/esmo_explore.py --port 5555
  python scripts/esmo_explore.py --no-png         # dumps only, smaller zip

Commands at the prompt
----------------------
  <Enter>            snapshot the current screen
  <any text>         snapshot, labelled with that text  (e.g.  draft_screen)
  t <x> <y>          tap, then snapshot
  d / u              page down / page up, then snapshot
  scan [label]       scroll to top, then page down to the end, capturing all of it
  b                  Back, then snapshot
  find <text>        search the current screen, print matches with coordinates
  raw                print the last snapshot including layout-only nodes
  q                  quit and zip

Everything lands in esmo_explore/ and is zipped as esmo_explore.zip.
"""

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

# Output lands in the directory you run from, not beside the script: an installed
# copy lives in site-packages, which is no place for capture data.
OUT_ROOT = pathlib.Path.cwd()
OUT = OUT_ROOT / "esmo_explore"
PACKAGE = "gg.esmo"

CANDIDATE_PORTS = [5555, 5556, 5557, 5565, 5575, 5585,
                   62001, 62025, 62026, 21503, 21513]

# Swipe gestures do NOT scroll Flutter views under BlueStacks - only page keys do.
PAGE_DOWN, PAGE_UP = "93", "92"
MAX_SCROLLS = 12

# Furniture that appears on nearly every screen; hidden unless you ask for raw.
CHROME = re.compile(r"^(Overview|Meta|Stats|Heroes|Draft|Strategies)\nTab \d of \d$")


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

    def key(self, code, wait=1.0):
        self.shell("input", "keyevent", code)
        time.sleep(wait)


def find_adb(explicit=None):
    if explicit:
        if pathlib.Path(explicit).exists():
            return explicit
        sys.exit(f"adb not found at: {explicit}")
    if shutil.which("adb"):
        return shutil.which("adb")
    # Platform-tools before BlueStacks - see the note in esmo_capture.find_adb:
    # HD-Adb's 1.0.36 client restarts the server on every command when a newer
    # adb owns it, which costs 5s per call.
    for g in [pathlib.Path.home() / r"AppData\Local\Android\Sdk\platform-tools\adb.exe",
              r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
              r"C:\platform-tools\adb.exe",
              r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
              r"C:\Program Files\BlueStacks_nxt\adb.exe",
              "/usr/local/bin/adb", "/usr/bin/adb"]:
        if pathlib.Path(g).exists():
            return str(g)
    sys.exit("Could not find adb. Pass --adb <path>.")


def connect(adb, port=None):
    for p in ([port] if port else CANDIDATE_PORTS):
        adb.run("connect", f"127.0.0.1:{p}", timeout=15)
    _, out, _ = adb.run("devices")
    return [ln.split("\t")[0].strip() for ln in out.splitlines()[1:]
            if "\t" in ln and ln.split("\t")[1].strip() == "device"]


def dump_xml(adb):
    for _ in range(3):
        rc, out, err = adb.shell("uiautomator", "dump", "/sdcard/esmo_ui.xml", timeout=60)
        if "ERROR" in ((out or "") + (err or "")).upper():
            time.sleep(1.0)
            continue
        rc, xml, _ = adb.shell("cat", "/sdcard/esmo_ui.xml", timeout=60)
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


def nodes_of(xml, with_empty=False):
    """The important bit: ESMO's text lives in content-desc, NOT in text."""
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for n in root.iter("node"):
        desc = (n.get("content-desc") or "").strip() or (n.get("text") or "").strip()
        if not desc and not with_empty:
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        out.append({
            "desc": desc, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "cls": (n.get("class") or "").split(".")[-1],
            "clickable": n.get("clickable") == "true",
            "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
        })
    out.sort(key=lambda r: (r["y1"], r["x1"]))
    return out


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def screencap(adb):
    rc, data, _ = adb.run("exec-out", "screencap", "-p", binary=True, timeout=60)
    if rc != 0 or not data:
        return None
    # Same chatter trap as dump_xml - adb's banner lands on STDOUT in front of
    # the PNG. Slice from the magic; unmangle CRLF only if that failed.
    i = data.find(PNG_MAGIC)
    if i < 0:
        data = data.replace(b"\r\n", b"\n")
        i = data.find(PNG_MAGIC)
    return data[i:] if i >= 0 else None


def signature(xml):
    return frozenset((n["desc"], n["y1"]) for n in nodes_of(xml))


def print_nodes(nodes, show_all=False):
    shown = 0
    for n in nodes:
        if not show_all and CHROME.match(n["desc"]):
            continue
        flag = "*" if n["clickable"] else " "
        text = n["desc"].replace("\n", " | ")
        if len(text) > 78:
            text = text[:75] + "..."
        print(f"   {flag} tap({n['cx']:4d},{n['cy']:4d}) "
              f"[{n['x1']},{n['y1']}][{n['x2']},{n['y2']}] "
              f"{n['cls']:11} {text!r}")
        shown += 1
    if not shown:
        print("   (no text nodes - the screen may still be loading, or this area "
              "is drawn as an image)")
    print(f"   -- {shown} text node(s); * = tappable")


class Session:
    def __init__(self, adb, save_png=True):
        self.adb, self.save_png = adb, save_png
        self.i = 0
        self.last = []

    def snap(self, label=None, quiet=False):
        self.i += 1
        tag = f"{self.i:02d}" + (f"_{label}" if label else "")
        xml = dump_xml(self.adb)
        if xml:
            (OUT / f"{tag}.xml").write_text(xml, encoding="utf-8")
        if self.save_png:
            png = screencap(self.adb)
            if png:
                (OUT / f"{tag}.png").write_bytes(png)
        nodes = nodes_of(xml)
        self.last = nodes
        if not quiet:
            print(f"\n  --- {tag}")
            print_nodes(nodes)
        return xml, nodes

    def scan(self, label=None):
        """Page through a scrollable screen and capture every distinct state."""
        for _ in range(8):
            self.adb.key(PAGE_UP, wait=0.4)
        seen, captured = [], 0
        for _ in range(MAX_SCROLLS):
            xml = dump_xml(self.adb)
            if not xml:
                break
            sig = signature(xml)
            if sig in seen:
                break
            seen.append(sig)
            captured += 1
            self.i += 1
            tag = f"{self.i:02d}_{label or 'scan'}_{captured:02d}"
            (OUT / f"{tag}.xml").write_text(xml, encoding="utf-8")
            if self.save_png:
                png = screencap(self.adb)
                if png:
                    (OUT / f"{tag}.png").write_bytes(png)
            nodes = nodes_of(xml)
            print(f"\n  --- {tag}")
            print_nodes(nodes)
            self.adb.key(PAGE_DOWN, wait=1.0)
        print(f"\n  scan complete: {captured} screen(s) of content")
        if captured == 1:
            print("  (one screen only - either it all fits, or this view does "
                  "not scroll)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()

    adb_path = find_adb(args.adb)
    devices = connect(Adb(adb_path), args.port)
    if not devices:
        sys.exit("No adb device. Is BlueStacks running with ADB enabled?")
    adb = Adb(adb_path, devices[0])
    print(f"adb: {adb_path}\ndevice: {devices[0]}")

    _, size, _ = adb.shell("wm", "size")
    print(f"screen: {(size or '').strip()}")
    if "1920x1080" not in (size or ""):
        print("!! Coordinates in the existing scripts assume 1920x1080.")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()

    print("\n" + "=" * 70)
    print("Navigate to any page in ESMO, then type a label and press Enter.")
    print("  <Enter>=snapshot   <text>=snapshot labelled   t x y=tap")
    print("  d/u=page down/up   scan=capture whole scrollable page")
    print("  b=back   find <text>=locate text   raw=show layout nodes   q=quit")
    print("=" * 70)

    s = Session(adb, save_png=not args.no_png)

    while True:
        try:
            cmd = input(f"\n[{s.i:02d}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        low = cmd.lower()
        if low in ("q", "quit", "exit"):
            break

        if low == "raw":
            print_nodes(s.last, show_all=True)
            continue

        if low.startswith("find "):
            needle = cmd[5:].strip().lower()
            hits = [n for n in s.last if needle in n["desc"].lower()]
            if hits:
                print_nodes(hits, show_all=True)
            else:
                print("   no match on the last snapshot (take a new one first?)")
            continue

        parts = cmd.split()
        head = parts[0].lower() if parts else ""

        if head == "t" and len(parts) == 3:
            adb.tap(int(parts[1]), int(parts[2]))
            s.snap()
        elif head == "d":
            adb.key(PAGE_DOWN)
            s.snap()
        elif head == "u":
            adb.key(PAGE_UP)
            s.snap()
        elif head == "b":
            adb.key("4")
            s.snap()
        elif head == "scan":
            s.scan(parts[1] if len(parts) > 1 else None)
        elif head in ("t", "find"):
            print("   usage: t <x> <y>   |   find <text>")
        elif cmd:
            s.snap(re.sub(r"[^A-Za-z0-9_-]+", "_", cmd))
        else:
            s.snap()

    if s.i:
        zpath = OUT_ROOT / "esmo_explore.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(OUT.iterdir()):
                zf.write(f, f.name)
        mb = zpath.stat().st_size / 1e6
        print(f"\n{s.i} snapshot(s) -> {zpath} ({mb:.1f} MB)")
        print("Send that zip to Claude to get a capture script for this page.")
    else:
        print("\nnothing captured")


if __name__ == "__main__":
    main()
