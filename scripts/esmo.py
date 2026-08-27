#!/usr/bin/env python3
"""
Run a capture and parse it in one go, with the flag combinations you actually use.

  python scripts/esmo.py                 # menu
  python scripts/esmo.py weekly          # capture with the weekly preset, then parse
  python scripts/esmo.py weekly --redo Brewer,Nomad
  python scripts/esmo.py list            # show what each preset runs
  python scripts/esmo.py weekly -n       # print the commands, run nothing

Parsed output lands in --out-dir as YYYYMMDD_champions.json. Anything after the
preset name is passed straight to esmo_capture.py, so one-off flags still work:

  python scripts/esmo.py weekly --port 5555 --limit 3

Edit PRESETS below to add your own.
"""

import argparse
import datetime
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CAPTURE = HERE / "esmo_capture.py"
PARSE = HERE / "parse_esmo.py"
# esmo_capture.py writes next to itself, not to the current directory
CAPTURE_DIR = HERE / "esmo_capture"

PRESETS = {
    "weekly":  ["--range", "7d", "--resume", "--scroll-method", "pagekeys"],
    "daily":   ["--range", "24h", "--resume", "--scroll-method", "pagekeys"],
    "monthly": ["--range", "28d", "--resume", "--scroll-method", "pagekeys"],
    "quick":   ["--no-meta", "--resume", "--scroll-method", "pagekeys"],
    "fresh":   ["--range", "7d", "--scroll-method", "pagekeys"],
}
DESCRIPTIONS = {
    "weekly":  "7-day meta, resumes an interrupted run. The usual one.",
    "daily":   "24-hour meta. Thin sample - matchup counts get small.",
    "monthly": "28-day meta.",
    "quick":   "Skips the Meta tab: abilities, stats and portraits only (~15 min).",
    "fresh":   "7-day meta, ignores captured.json and walks the whole roster.",
}


def commands(preset, extra, out_dir, stamp):
    """The two commands a run issues, as argv lists."""
    out = pathlib.Path(out_dir) / f"{stamp}_champions.json"
    return [
        [sys.executable, str(CAPTURE), *PRESETS[preset], *extra],
        [sys.executable, str(PARSE), "--dir", str(CAPTURE_DIR), "--out", str(out)],
    ]


def choose():
    names = list(PRESETS)
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name:8} {DESCRIPTIONS[name]}")
    reply = input("\npreset (number or name, blank to cancel): ").strip()
    if not reply:
        return None
    if reply.isdigit() and 1 <= int(reply) <= len(names):
        return names[int(reply) - 1]
    return reply if reply in PRESETS else None


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("preset", nargs="?", help="one of: " + ", ".join(PRESETS) + ", or 'list'")
    ap.add_argument("--out-dir", default=".", help="where the parsed JSON lands (default: .)")
    ap.add_argument("-n", "--dry-run", action="store_true", help="print the commands only")
    ap.add_argument("-h", "--help", action="help")
    args, extra = ap.parse_known_args()

    if args.preset == "list":
        for name in PRESETS:
            print(f"{name:8} {DESCRIPTIONS[name]}\n         esmo_capture.py {' '.join(PRESETS[name])}")
        return 0

    preset = args.preset or choose()
    if preset is None:
        return 1
    if preset not in PRESETS:
        sys.exit(f"unknown preset {preset!r}. Known: {', '.join(PRESETS)}")

    stamp = datetime.date.today().strftime("%Y%m%d")
    pathlib.Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    for cmd in commands(preset, extra, args.out_dir, stamp):
        print("\n>", " ".join(cmd[1:]))
        if args.dry_run:
            continue
        result = subprocess.run(cmd)
        if result.returncode != 0:
            # A capture stopped with Ctrl+C is resumable; parsing half a roster is not.
            sys.exit(f"\n{pathlib.Path(cmd[1]).name} exited {result.returncode} - stopping here. "
                     f"Re-run with the same preset to resume.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
