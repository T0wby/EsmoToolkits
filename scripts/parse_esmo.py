#!/usr/bin/env python3
"""
Turn an esmo_capture/ folder of raw uiautomator dumps into champions.json.

  python scripts/parse_esmo.py                    # reads ./esmo_capture, writes champions.json
  python scripts/parse_esmo.py --dir some/folder --out out.json

Runs entirely offline on already-captured data, so it is safe to re-run and tweak.
"""

import argparse
import collections
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROLE_ORDER = ["Top", "Jungle", "Mid", "Bot", "Support"]
ATTACK_TYPES = {"Ranged", "Melee"}
DAMAGE_TYPES = {"Magic", "Physical", "Mixed", "True"}
SLOTS = ("Q", "W", "E", "R")

RE_SCALING = re.compile(r"^\d+(?:\.\d+)?\s*\(\+[\d.]+/lvl\)$")
RE_RESOURCE = re.compile(r"^(\d+(?:\.\d+)?\s*\(\+[\d.]+/lvl\))\s+(\w+)$")
RE_PERIOD = re.compile(r"^[A-Z][a-z]{2} \d{1,2} - [A-Z][a-z]{2} \d{1,2}$")
RE_WINBLOCK = re.compile(r"^(\d+\.\d+)%\n(\d+)W (\d+)L")
RE_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def nodes_of(path):
    """[(desc, x1, y1, x2, y2, cls, clickable)] sorted by y then x."""
    try:
        root = ET.fromstring(pathlib.Path(path).read_text(encoding="utf-8"))
    except (ET.ParseError, OSError):
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
    out.sort(key=lambda r: (r[2], r[1]))
    return out


def is_chrome(desc):
    """Header/tab furniture that appears on every screen."""
    return (desc in ("Back", "Playbook")
            or re.match(r"^(Overview|Meta|Stats|Heroes|Draft|Strategies)\nTab \d of \d$", desc))


def num(s, cast=float):
    m = RE_NUM.search(s or "")
    return cast(m.group()) if m else None


# ---------------------------------------------------------------- overview
def parse_ability(desc):
    p = [x.strip() for x in desc.split("\n") if x.strip()]
    if len(p) < 3:
        return None
    ability = {
        "slot": p[0],
        "targeting": p[1],
        "cooldown": None,
        "range": None,
        "scaling": None,
        "effects": [],
        "raw": desc,
    }
    body = p[2:]
    # trailing bare number is the ability range
    if body and re.match(r"^\d+(?:\.\d+)?$", body[-1]):
        ability["range"] = float(body.pop())
    # then a cooldown like "110s"
    if body and re.match(r"^\d+(?:\.\d+)?s$", body[-1]):
        ability["cooldown"] = body.pop()
    for line in body:
        if "(+" in line and ability["scaling"] is None:
            ability["scaling"] = line
        else:
            ability["effects"].append(line)
    return ability


def parse_overview(files):
    out = {
        "class": None, "class_description": None,
        "attack_type": None, "damage_type": None,
        "base_health": None, "base_resource": None, "resource_type": None,
        "abilities": [],
    }
    seen_slots = set()
    for f in files:
        for desc, x1, y1, x2, y2, cls, ck in nodes_of(f):
            if is_chrome(desc):
                continue

            if desc.split("\n")[0] in SLOTS and "\n" in desc:
                slot = desc.split("\n")[0]
                if slot not in seen_slots:
                    ab = parse_ability(desc)
                    if ab:
                        out["abilities"].append(ab)
                        seen_slots.add(slot)
                continue

            if desc in ATTACK_TYPES:
                out["attack_type"] = out["attack_type"] or desc
                continue
            if desc in DAMAGE_TYPES:
                out["damage_type"] = out["damage_type"] or desc
                continue

            if RE_SCALING.match(desc):
                out["base_health"] = out["base_health"] or desc
                continue
            m = RE_RESOURCE.match(desc)
            if m:
                out["base_resource"] = out["base_resource"] or m.group(1)
                out["resource_type"] = out["resource_type"] or m.group(2)
                continue

            # "Battle Mage\nShort-range mages who wade into combat..."
            if "\n" in desc and out["class"] is None and len(desc) > 40:
                head, _, tail = desc.partition("\n")
                if len(head) < 40:
                    out["class"] = head.strip()
                    # A transient render can concatenate two class cards into one
                    # node ("Diver\n...\nBattle Mage\n..."). Descriptions are a
                    # single paragraph, so keep only the first.
                    out["class_description"] = tail.strip().split("\n")[0].strip()

    out["abilities"].sort(key=lambda a: SLOTS.index(a["slot"]) if a["slot"] in SLOTS else 9)
    return out


# ---------------------------------------------------------------- meta
RE_OPPONENT = re.compile(r"^[A-Za-z][A-Za-z0-9 '\-]{1,}$")


def parse_matchup(desc):
    p = [x.strip() for x in desc.split("\n") if x.strip()]
    # ['Automata','vs','Gambler','67.6%','WR','34','G']
    if len(p) < 4 or p[1] != "vs":
        return None
    # The cached placeholder emits rows like 'X vs 1 52.0%'. A real opponent is a
    # champion name, never a bare number.
    if not RE_OPPONENT.match(p[2]):
        return None
    return {
        "opponent": p[2],
        "win_rate": num(p[3]),
        "games": next((int(float(x)) for x in p[4:] if re.match(r"^\d+$", x)), None),
    }


def parse_meta(files):
    meta = {
        "period": None, "pick_rate": None, "ban_rate": None,
        "win_rate": None, "wins": None, "losses": None, "games": None,
        "kda": None, "gold_per_min": None, "cs_per_min": None,
        "damage_per_min": None, "vision_per_game": None,
        "best_matchups": [], "worst_matchups": [],
    }
    best_seen, worst_seen = {}, {}
    win_candidates, pickban_candidates = [], []

    for f in files:
        section = None
        for desc, x1, y1, x2, y2, cls, ck in nodes_of(f):
            if is_chrome(desc):
                continue

            if desc == "Best matchups":
                section = "best"
                continue
            if desc == "Worst matchups":
                section = "worst"
                continue

            if RE_PERIOD.match(desc):
                meta["period"] = meta["period"] or desc
                continue

            if desc.startswith("Pick rate"):
                p = desc.split("\n")
                cand = {}
                for i, tok in enumerate(p):
                    if tok == "Pick rate" and i + 1 < len(p):
                        cand["pick_rate"] = num(p[i + 1])
                    if tok == "Ban rate" and i + 1 < len(p):
                        cand["ban_rate"] = num(p[i + 1])
                if cand:
                    cand["_file"] = str(f)
                    pickban_candidates.append(cand)
                continue

            m = RE_WINBLOCK.match(desc)
            if m:
                p = desc.split("\n")
                gm = re.search(r"·\s*(\d+)\s*games", desc)
                cand = {
                    "win_rate": float(m.group(1)),
                    "wins": int(m.group(2)),
                    "losses": int(m.group(3)),
                    "games": int(gm.group(1)) if gm else None,
                }
                labels = {"KDA": "kda", "Gold/min": "gold_per_min",
                          "CS/min": "cs_per_min", "Damage/min": "damage_per_min",
                          "Vision/game": "vision_per_game"}
                for i, tok in enumerate(p):
                    if tok in labels and i + 1 < len(p):
                        cand[labels[tok]] = num(p[i + 1])
                cand["_file"] = str(f)
                win_candidates.append(cand)
                continue

            if "\nvs\n" in desc:
                mu = parse_matchup(desc)
                if not mu:
                    continue
                bucket = section or ("best" if (mu["win_rate"] or 0) >= 50 else "worst")
                store = best_seen if bucket == "best" else worst_seen
                prev = store.get(mu["opponent"])
                # The same matchup appears in several overlapping scroll dumps;
                # keep whichever row carried the larger sample.
                if prev is None or (mu["games"] or 0) > (prev["games"] or 0):
                    store[mu["opponent"]] = mu

    # The Meta tab briefly shows a small cached sample before the server aggregates
    # arrive. If more than one distinct block was captured, trust the largest sample.
    chosen_file = None
    if win_candidates:
        chosen = max(win_candidates, key=lambda c: c["games"] or 0)
        chosen_file = chosen["_file"]
        meta.update({k: v for k, v in chosen.items()
                     if v is not None and k != "_file"})
        distinct = {c["games"] for c in win_candidates if c["games"]}
        if len(distinct) > 1:
            meta["sample_warning"] = (
                f"multiple sample sizes seen {sorted(distinct)}; "
                f"kept {chosen['games']} (largest)")

    if pickban_candidates:
        # Must come from the same dump as the win block - the cached view has its
        # own (much higher) pick rate, and mixing the two would be silent nonsense.
        same = [c for c in pickban_candidates if c["_file"] == chosen_file]
        pb = (same or pickban_candidates)[0]
        meta["pick_rate"] = pb.get("pick_rate")
        meta["ban_rate"] = pb.get("ban_rate")
        if not same and chosen_file:
            meta["pickban_warning"] = "pick/ban taken from a different dump than win rate"

    meta["best_matchups"] = sorted(best_seen.values(),
                                   key=lambda m: -(m["win_rate"] or 0))
    meta["worst_matchups"] = sorted(worst_seen.values(),
                                    key=lambda m: (m["win_rate"] or 0))
    return meta


# ---------------------------------------------------------------- stats
def looks_like_section(desc):
    return (desc == desc.upper() and len(desc) > 2
            and re.match(r"^[A-Z0-9 ()/-]+$", desc) and not RE_NUM.match(desc))


def parse_stats(files):
    """Label/value rows grouped under their section header.

    Section is deliberately NOT carried between files. Consecutive scroll dumps
    overlap, so a row that starts a file (its header scrolled off the top) always
    reappears further down another file WITH its header visible. Carrying the
    section across files instead attributed such rows to whatever section the
    previous file happened to end in - which put the ARMOR value under
    PENETRATION and lost PENETRATION's real one.
    """
    stats = {}
    for f in files:
        rows = {}
        for desc, x1, y1, x2, y2, cls, ck in nodes_of(f):
            if is_chrome(desc) or desc in ("Level", "N"):
                continue
            rows.setdefault(y1, []).append((x1, desc, cls))

        section = None          # reset per file - see docstring
        for y1 in sorted(rows):
            items = sorted(rows[y1])
            if len(items) == 1:
                only = items[0][1]
                if looks_like_section(only):
                    section = only
                continue
            if len(items) >= 2:
                label = items[0][1]
                value = items[-1][1]
                if looks_like_section(label):
                    section = label
                    continue
                if section is None:
                    continue    # header is above this screen; caught elsewhere
                bucket = stats.setdefault(section, {})
                if label not in bucket:
                    bucket[label] = value
    return stats


# ------------------------------------------------------- playable positions
# A position the champion plays but has no games in this window shows "Not enough
# data" instead of a win/loss block, so it produces no meta rows at all - and the
# position itself used to vanish with them. The game states it twice more, and
# both are already on disk: the icon row under the champion name has exactly one
# icon per playable position, and the Meta tab's selector draws playable icons
# lit (~227) against greyed-out ones (~125). Neither needs a re-capture.
ROLE_STRIP_ENABLED_MAX = 180        # keep in sync with esmo_capture.ROLE_ENABLED_MAX


def header_role_count(files):
    """How many position icons sit under the champion name."""
    best = 0
    for f in files:
        try:
            root = ET.fromstring(pathlib.Path(f).read_text(encoding="utf-8"))
        except (ET.ParseError, OSError):
            continue
        n = 0
        for nd in root.iter("node"):
            if not (nd.get("class") or "").endswith("ImageView"):
                continue
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", nd.get("bounds") or "")
            if m:
                x1, y1, x2, y2 = map(int, m.groups())
                if 190 <= y1 <= 210 and y2 - y1 < 40:
                    n += 1
        best = max(best, n)
    return best


def strip_roles(png, expect):
    """Playable positions read from the saved position-selector crop.

    Returns None when it cannot be trusted. The strip is one frame: an unstyled
    one paints every icon lit, which would invent positions. `expect` (the header
    icon count) is the independent second opinion - if the two disagree, say
    nothing rather than guess.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(png).convert("L")
    except OSError:
        return None
    w = im.width / len(ROLE_ORDER)
    roles = [ROLE_ORDER[i] for i in range(len(ROLE_ORDER))
             if im.crop((int(i * w), 0, int((i + 1) * w), im.height)).getextrema()[1]
             >= ROLE_STRIP_ENABLED_MAX]
    return roles if roles and len(roles) == expect else None


# ---------------------------------------------------------------- driver
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="esmo_capture")
    ap.add_argument("--out", default="champions.json")
    ap.add_argument("--merge", metavar="FILE",
                    help="fill positions/meta from an existing champions.json "
                         "for champions this run captured without meta "
                         "(e.g. after a --no-meta run)")
    ap.add_argument("--strict-period", action="store_true",
                    help="drop positions whose date window differs from the "
                         "majority, so one file never mixes two windows")
    args = ap.parse_args()

    root = pathlib.Path(args.dir)
    raw = root / "raw"
    if not raw.is_dir():
        sys.exit(f"no raw/ folder under {root}")

    # captured.json records the order positions were visited in, which is what
    # makes the duplicate-position repair unambiguous.
    capture_order, meta_attempted = {}, {}
    cj = root / "captured.json"
    if cj.exists():
        try:
            for c in json.loads(cj.read_text(encoding="utf-8"))["champions"]:
                if c.get("roles"):
                    capture_order[c["dir"]] = c["roles"]
                # Whether the Meta tab was opened at all this run. A champion with
                # no games anywhere leaves no meta files, and the saved position
                # strip is the only record that it has positions - but on a
                # --no-meta run that strip is a leftover from an earlier capture
                # and must not be read as current.
                meta_attempted[c["dir"]] = (
                    any(k.startswith("meta") for k in (c.get("files") or {}))
                    or "meta" in (c.get("incomplete") or []))
        except (ValueError, KeyError):
            pass

    champions, problems, dropped_positions = [], [], []
    recovered_positions, unreadable_strips = [], []
    stale_files = []
    for cdir in sorted(raw.iterdir()):
        if not cdir.is_dir():
            continue
        ov = sorted(cdir.glob("overview_*.xml"))
        st = sorted(cdir.glob("stats_*.xml"))

        # meta_<Position>_NN.xml (per position), or legacy meta_NN.xml
        by_role = {}
        legacy = []
        # captured.json is the authority on which positions this champion really
        # has. Files for any other position are leftovers from an earlier run -
        # older data that would otherwise be parsed as current and silently mix
        # two capture dates together.
        allowed = capture_order.get(cdir.name)
        for f in sorted(cdir.glob("meta_*.xml")):
            m = re.match(r"^meta_([A-Za-z]+)_\d+\.xml$", f.name)
            if m:
                if allowed is not None and m.group(1) not in allowed:
                    stale_files.append(f"{cdir.name}/{f.name}")
                    continue
                by_role.setdefault(m.group(1), []).append(f)
            elif re.match(r"^meta_\d+\.xml$", f.name):
                legacy.append(f)

        entry = {"name": cdir.name}
        entry.update(parse_overview(ov))

        # Walk positions in CAPTURE order. Tapping a position the champion can't
        # play leaves the previous one's data on screen, producing a byte-identical
        # duplicate; the first occurrence is the real one.
        order = capture_order.get(cdir.name) or sorted(
            by_role, key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else 99)
        order = [r for r in order if r in by_role] + \
                [r for r in by_role if r not in order]

        positions, seen_samples = [], {}
        for role in order:
            pm = parse_meta(by_role[role])
            if pm["win_rate"] is None:
                continue
            fp = (pm["wins"], pm["losses"], pm["games"])
            if None not in fp and fp in seen_samples:
                dropped_positions.append(
                    f"{cdir.name}: {role} identical to {seen_samples[fp]} "
                    f"({fp[0]}W {fp[1]}L) - not actually playable")
                continue
            seen_samples[fp] = role
            positions.append({"position": role, **pm})

        # Positions the champion plays that had no games in this window: keep the
        # position, with null numbers and a no_data flag so nothing reads them as
        # a real sample.
        strip = root / "portraits" / f"_rolestrip_{cdir.name}.png"
        if meta_attempted.get(cdir.name, bool(by_role or legacy)) and strip.exists():
            played = strip_roles(strip, header_role_count(ov))
            if played is None:
                unreadable_strips.append(cdir.name)
            else:
                have = {p["position"] for p in positions}
                for role in played:
                    if role not in have:
                        positions.append({"position": role, **parse_meta([]),
                                          "no_data": True})
                        recovered_positions.append(f"{cdir.name}: {role}")

        positions.sort(key=lambda p: ROLE_ORDER.index(p["position"])
                       if p["position"] in ROLE_ORDER else 99)
        entry["positions"] = positions
        with_data = [p for p in positions if p.get("win_rate") is not None]
        entry["meta"] = parse_meta(legacy) if legacy else (
            {k: v for k, v in with_data[0].items() if k not in ("position", "no_data")}
            if with_data else parse_meta([]))
        entry["stats"] = parse_stats(st)

        p = root / "portraits" / f"{cdir.name}.png"
        entry["portrait"] = f"portraits/{cdir.name}.png" if p.exists() else None

        missing = []
        if not entry["abilities"]:
            missing.append("abilities")
        if entry["base_health"] is None:
            missing.append("base_health")
        # Only a concern when meta was actually captured - a --no-meta run has
        # none by design.
        if (by_role or legacy) and entry["meta"]["win_rate"] is None:
            missing.append("win_rate")
        if not entry["stats"]:
            missing.append("stats")
        if missing:
            problems.append((cdir.name, missing))

        champions.append(entry)

    # A --no-meta run has no positions and an all-null meta block, which is
    # structurally valid but useless to a consumer. Fill those in from a previous
    # capture when asked, and mark clearly where each champion's meta came from.
    merged = []
    if args.merge:
        try:
            src = json.loads(pathlib.Path(args.merge).read_text(encoding="utf-8"))
            by_name = {c["name"]: c for c in src.get("champions", [])}
        except (OSError, ValueError, KeyError) as exc:
            sys.exit(f"could not read --merge file {args.merge}: {exc}")
        for c in champions:
            if c["positions"] or c["meta"].get("win_rate") is not None:
                continue
            other = by_name.get(c["name"])
            if not other or not other.get("positions"):
                continue
            c["positions"] = other["positions"]
            c["meta"] = other.get("meta", c["meta"])
            c["meta_source"] = args.merge
            merged.append(c["name"])

    for c in champions:
        # A position with no games is still a known position, but not meta.
        c["has_meta"] = any(p.get("win_rate") is not None for p in c["positions"])

    payload = {
        "source": "ESMO in-game capture",
        "champion_count": len(champions),
        "with_meta": sum(1 for c in champions if c["has_meta"]),
        "champions": champions,
    }
    pathlib.Path(args.out).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"parsed {len(champions)} champions -> {args.out}")
    if merged:
        print(f"  merged meta for {len(merged)} champion(s) from {args.merge}")
    no_meta = [c["name"] for c in champions if not c["has_meta"]]
    if no_meta:
        print(f"  {len(no_meta)} champion(s) have NO positions/meta "
              f"(has_meta=false)")
        if not args.merge:
            print("    to fill them from a previous capture:")
            print("    python scripts/parse_esmo.py --merge champions_previous.json")
    ab = sum(len(c["abilities"]) for c in champions)
    mu = sum(len(c["meta"]["best_matchups"]) + len(c["meta"]["worst_matchups"])
             for c in champions)
    sv = sum(len(v) for c in champions for v in c["stats"].values())
    # Every position records the date range its numbers came from. If the filter
    # failed to apply on some champion, that shows up here as a second window -
    # which would otherwise be invisible in the data.
    periods = collections.Counter(p["period"] for c in champions
                                  for p in c["positions"] if p.get("period"))
    if periods:
        print("  date ranges seen:")
        for per, n in periods.most_common():
            print(f"    {per:22} {n:4d} position(s)")
        if len(periods) > 1:
            odd = periods.most_common()[1:]
            print(f"  !! MIXED WINDOWS - {sum(n for _, n in odd)} position(s) "
                  f"differ from the majority; those rows are not comparable")
            if args.strict_period:
                keep = periods.most_common(1)[0][0]
                culled = []
                for c in champions:
                    before = len(c["positions"])
                    c["positions"] = [p for p in c["positions"]
                                      if p.get("period") in (keep, None)]
                    if len(c["positions"]) != before:
                        culled.append(c["name"])
                print(f"  --strict-period: kept only {keep!r}; "
                      f"trimmed {len(culled)} champion(s): {', '.join(culled)}")
                payload["champions"] = champions
                pathlib.Path(args.out).write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")

    pos = sum(len(c["positions"]) for c in champions)
    nometa = sum(1 for c in champions if not c["positions"]
                 and c["meta"]["win_rate"] is None)
    print(f"  abilities: {ab}   matchups: {mu}   stat values: {sv}")
    print(f"  positions: {pos}" + (f"   (no meta captured: {nometa})" if nometa else ""))
    if stale_files:
        chs = sorted({f.split("/")[0] for f in stale_files})
        print(f"\n  ignored {len(stale_files)} stale file(s) from a previous run, "
              f"across {len(chs)} champion(s):")
        print(f"    {', '.join(chs)}")
        print("    (delete esmo_capture/ before a fresh full run, or re-capture "
              "those champions, to clear them from disk)")

    if recovered_positions:
        print(f"\n  {len(recovered_positions)} position(s) with no games in this "
              f"window (kept, no_data=true):")
        for line in recovered_positions:
            print(f"    {line}")
    if unreadable_strips:
        print(f"\n  could not read the position selector for "
              f"{len(unreadable_strips)} champion(s): {', '.join(unreadable_strips)}")
        print("    (a position they play but have no games in may be missing)")

    if dropped_positions:
        print(f"\n  dropped {len(dropped_positions)} phantom position(s):")
        for line in dropped_positions:
            print(f"    {line}")

    if problems:
        print(f"\n  {len(problems)} champion(s) with gaps:")
        for name, miss in problems[:20]:
            print(f"    {name}: missing {', '.join(miss)}")


if __name__ == "__main__":
    main()
