#!/usr/bin/env python3
"""
Run a capture and parse it in one go, with the flag combinations you actually use.

  python scripts/esmo.py                 # menu
  python scripts/esmo.py weekly          # capture with the weekly preset, then parse
  python scripts/esmo.py weekly --redo Brewer,Nomad
  python scripts/esmo.py list            # show what each preset runs
  python scripts/esmo.py weekly -n       # print the commands, run nothing

Anything after the preset name goes straight to esmo_capture.py. Anything after a bare
`--` goes to parse_esmo.py instead, so both halves of the run are reachable:

  python scripts/esmo.py weekly --port 5555 -- --strict-period

Flags you pass override the preset's own - argparse takes the last occurrence - so
`esmo.py weekly --range 14d` is a 14-day weekly. Give flags with no preset name at all
and the run uses exactly those flags.

Parsed output lands in --out-dir named by --pattern (default `{date}_champions.json`,
placeholders `{date}` `{preset}` `{range}`), or at the literal path given to --out.

Presets live in PRESETS below and in ~/.esmo.json, which wins on a name clash. The GUI
(esmo_gui.py) writes that file; nothing stops you editing it by hand:

  {"pattern": "{date}_{range}.json",
   "presets": {"biweekly": {"capture": ["--range", "14d", "--resume"],
                            "parse": ["--strict-period"],
                            "description": "14-day meta"}}}

Top-level keys are yours. The "state" key is the GUI's memory of its own widgets and is
ignored here.
"""

import argparse
import datetime
import json
import pathlib
import shlex
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CAPTURE = HERE / "esmo_capture.py"
PARSE = HERE / "parse_esmo.py"
# both scripts anchor their output at the working directory
CAPTURE_DIR = pathlib.Path.cwd() / "esmo_capture"
CONFIG = pathlib.Path.home() / ".esmo.json"

DEFAULT_PATTERN = "{date}_champions.json"
DEFAULT_RANGE = "7d"        # esmo_capture.py's own --range default

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

# Widget field <-> flag, shared by the GUI and by the inverse mapping below.
BOOL_FLAGS = {"--resume": "resume", "--no-meta": "no_meta", "--no-roles": "no_roles"}
VALUE_FLAGS = {"--range": "range", "--phase": "phase", "--scroll-method": "scroll_method",
               "--limit": "limit", "--port": "port"}


# ---------------------------------------------------------------- config
def _norm(entry, description=""):
    """A preset is either a bare list of capture flags or {capture, parse, description}."""
    if isinstance(entry, dict):
        return {"capture": list(entry.get("capture", [])),
                "parse": list(entry.get("parse", [])),
                "description": entry.get("description", description)}
    return {"capture": list(entry), "parse": [], "description": description}


def load_config(path=CONFIG):
    """~/.esmo.json, or {} when it is missing or unreadable. Never fatal: a typo in a
    config file should not stand between you and a capture."""
    try:
        cfg = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def save_config(cfg, path=CONFIG):
    pathlib.Path(path).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def load_presets(cfg=None):
    """Built-ins with the config layered over the top. Pass cfg={} for built-ins only."""
    merged = {n: _norm(f, DESCRIPTIONS[n]) for n, f in PRESETS.items()}
    cfg = load_config() if cfg is None else cfg
    for name, entry in (cfg.get("presets") or {}).items():
        merged[name] = _norm(entry)
    return merged


# ---------------------------------------------------------------- widget state <-> argv
def split_extra(text):
    """Free-text flags -> argv. posix=False keeps Windows backslashes intact; the quotes
    it leaves around a token have to come off before subprocess sees it."""
    quotes = "\"'"
    return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in quotes else t
            for t in shlex.split(text or "", posix=False)]


def settings_to_args(s):
    """GUI widget state -> (capture argv, parse argv). Pure, so the GUI builds no command
    lines itself and this stays testable without opening a window."""
    cap = []
    for flag, field in VALUE_FLAGS.items():
        if s.get(field):
            cap += [flag, str(s[field])]
    for flag, field in BOOL_FLAGS.items():
        if s.get(field):
            cap.append(flag)
    cap += split_extra(s.get("extra", ""))

    par = []
    if s.get("merge"):
        par += ["--merge", str(s["merge"])]
    if s.get("strict_period"):
        par.append("--strict-period")
    par += split_extra(s.get("parse_extra", ""))
    return cap, par


def args_to_settings(capture_args, parse_args=()):
    """Inverse, for loading a preset into the GUI. Flags it doesn't know survive in the
    extras box instead of being silently dropped."""
    s, extra, i = {}, [], 0
    capture_args = list(capture_args)
    while i < len(capture_args):
        a = capture_args[i]
        if a in BOOL_FLAGS:
            s[BOOL_FLAGS[a]] = True
        elif a in VALUE_FLAGS and i + 1 < len(capture_args):
            s[VALUE_FLAGS[a]] = capture_args[i + 1]
            i += 1
        else:
            extra.append(a)
        i += 1
    s["extra"] = " ".join(extra)

    pextra, j = [], 0
    parse_args = list(parse_args)
    while j < len(parse_args):
        a = parse_args[j]
        if a == "--strict-period":
            s["strict_period"] = True
        elif a == "--merge" and j + 1 < len(parse_args):
            s["merge"] = parse_args[j + 1]
            j += 1
        else:
            pextra.append(a)
        j += 1
    s["parse_extra"] = " ".join(pextra)
    return s


# ---------------------------------------------------------------- command building
def resolve_range(capture_args):
    """The --range the capture will really use: last occurrence wins, as argparse does."""
    rng = DEFAULT_RANGE
    for i, a in enumerate(capture_args):
        if a == "--range" and i + 1 < len(capture_args):
            rng = capture_args[i + 1]
        elif a.startswith("--range="):
            rng = a.split("=", 1)[1]
    return rng


def format_name(pattern, stamp, preset, rng):
    """Pattern -> filename. A typo'd placeholder raises KeyError here, which beats
    finding out three hours later from a filename with a brace in it."""
    return pattern.format(date=stamp, preset=preset or "custom", range=rng)


def build(capture_args, parse_args, out_dir, stamp, preset=None, pattern=DEFAULT_PATTERN,
          workdir=None, out=None):
    """The two commands a run issues, as argv lists."""
    if out is None:
        name = format_name(pattern, stamp, preset, resolve_range(capture_args))
        out = pathlib.Path(out_dir) / name
    cdir = pathlib.Path(workdir) / "esmo_capture" if workdir else CAPTURE_DIR
    return [
        [sys.executable, str(CAPTURE), *capture_args],
        [sys.executable, str(PARSE), "--dir", str(cdir), "--out", str(out), *parse_args],
    ]


def commands(preset, extra, out_dir, stamp, presets=None, pattern=DEFAULT_PATTERN,
             parse_extra=(), workdir=None, out=None):
    """As build(), but starting from a preset name. Preset flags come first, so anything
    passed after them wins."""
    entry = _norm((presets or load_presets(cfg={}))[preset]) if preset else _norm([])
    return build(entry["capture"] + list(extra), entry["parse"] + list(parse_extra),
                 out_dir, stamp, preset, pattern, workdir, out)


def split_argv(argv):
    """(before `--`, after `--`). argparse consumes a bare `--` itself, so this has to
    happen before it ever sees the list."""
    if "--" in argv:
        i = argv.index("--")
        return list(argv[:i]), list(argv[i + 1:])
    return list(argv), []


def take_preset(argv):
    """(preset or None, the rest). The preset is the first bare word, if there is one.

    argparse cannot do this: an optional positional next to unknown flags claims the
    *value* of the first one it doesn't recognise, so `esmo.py --range 14d` would run a
    preset called 14d rather than a capture with a 14-day range."""
    if argv and not argv[0].startswith("-"):
        return argv[0], list(argv[1:])
    return None, list(argv)


# ---------------------------------------------------------------- cli
def choose(presets):
    names = list(presets)
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name:9} {presets[name]['description']}")
    reply = input("\npreset (number or name, blank to cancel): ").strip()
    if not reply:
        return None
    if reply.isdigit() and 1 <= int(reply) <= len(names):
        return names[int(reply) - 1]
    return reply if reply in presets else None


def hold(enabled, code):
    """The GUI opens a console that closes the moment the run ends; keep it up long
    enough to read the summary."""
    if enabled:
        try:
            input("\npress Enter to close ")
        except EOFError:
            pass
    return code


def main(argv=None):
    raw = sys.argv[1:] if argv is None else list(argv)
    argv, parse_extra = split_argv(raw)
    named, argv = take_preset(argv)
    ap = argparse.ArgumentParser(add_help=False, usage="esmo.py [preset] [capture flags] "
                                                       "[-- parse flags]")
    ap.add_argument("--out-dir", default=".", help="where the parsed JSON lands (default: .)")
    ap.add_argument("--pattern", help="output filename: {date} {preset} {range}")
    ap.add_argument("--out", help="literal output path, ignoring --pattern")
    ap.add_argument("--parse-only", action="store_true", help="skip the capture step")
    ap.add_argument("--hold", action="store_true", help="wait for Enter before exiting")
    ap.add_argument("-n", "--dry-run", action="store_true", help="print the commands only")
    ap.add_argument("-h", "--help", action="help")
    args, extra = ap.parse_known_args(argv)

    cfg = load_config()
    presets = load_presets(cfg)
    pattern = args.pattern or cfg.get("pattern") or DEFAULT_PATTERN

    if named == "list":
        for name, p in presets.items():
            print(f"{name:9} {p['description']}")
            print(f"          esmo_capture.py {' '.join(p['capture'])}")
            if p["parse"]:
                print(f"          parse_esmo.py   {' '.join(p['parse'])}")
        return 0

    preset = named
    if preset is None and not raw:   # bare `esmo.py` and nothing else means "ask me"
        preset = choose(presets)
        if preset is None:
            return 1
    if preset is not None and preset not in presets:
        sys.exit(f"unknown preset {preset!r}. Known: {', '.join(presets)}")

    stamp = datetime.date.today().strftime("%Y%m%d")
    pathlib.Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    try:
        cmds = commands(preset, extra, args.out_dir, stamp, presets, pattern,
                        parse_extra, out=args.out)
    except (KeyError, IndexError) as e:
        sys.exit(f"bad placeholder {e} in pattern {pattern!r}. "
                 f"Known: {{date}}, {{preset}}, {{range}}")
    if args.parse_only:
        cmds = cmds[1:]

    for cmd in cmds:
        print("\n>", " ".join(cmd[1:]))
        if args.dry_run:
            continue
        result = subprocess.run(cmd)
        if result.returncode != 0:
            # A capture stopped with Ctrl+C is resumable; parsing half a roster is not.
            print(f"\n{pathlib.Path(cmd[1]).name} exited {result.returncode} - stopping here. "
                  f"Re-run with the same preset to resume.")
            return hold(args.hold, 1)
    return hold(args.hold, 0)


if __name__ == "__main__":
    sys.exit(main())
