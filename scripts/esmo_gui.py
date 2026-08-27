#!/usr/bin/env python3
"""
A window for esmo.py: set the flags, name the output, press Launch.

  python scripts/esmo_gui.py

The run itself opens in its own console window and behaves exactly as it does from the
command line - same output, same Ctrl+C - because it *is* the command line: this window
only builds an esmo.py invocation and spawns it. Nothing here parses or captures.

Settings and saved presets live in ~/.esmo.json, shared with esmo.py, so a preset saved
here is a preset `esmo.py <name>` can run.

Everything that turns widget state into a command lives in esmo.py (settings_to_args,
args_to_settings, build), which is what keeps it testable without opening a window.
"""

import datetime
import pathlib
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import esmo  # noqa: E402

# Windows opens the run in its own console. Everywhere else this is 0 and output goes to
# whatever terminal launched the window, which is what the CLI does anyway.
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

RANGES = ["", "24h", "7d", "28d", "any", "none"]
PHASES = ["", "both", "top", "bottom"]
SCROLLS = ["", "pagekeys", "swipe_800", "swipe_1500", "swipe_short", "swipe_leftcol",
           "swipe_300", "touchscreen_800"]

FIELDS = ("range", "phase", "scroll_method", "limit", "port", "extra",
          "merge", "parse_extra")
CHECKS = ("resume", "no_meta", "no_roles", "strict_period")

PAD = dict(padx=6, pady=3, sticky="w")

# Hover text. Every one ends in a concrete example, because the flag names alone are the
# thing you cannot remember without opening the README - which is the reason this window
# exists at all.
TIPS = {
    "workdir":
        "Working directory of the run. esmo_capture/ is created inside it, and\n"
        "'Parse only' reads it. Set this: a window opened from a shortcut would\n"
        "otherwise capture into whatever folder the shortcut points at.\n\n"
        "Example:  D:\\captures",
    "preset":
        "A named set of flags, from esmo.py and ~/.esmo.json.\n"
        "'Load' fills the fields below from it; from then on the fields win, so\n"
        "unticking something the preset sets does what it looks like it does.\n"
        "'Save as...' writes the current fields back as a new preset, which\n"
        "`python scripts/esmo.py <name>` can then run too.\n\n"
        "Example:  weekly",
    "range":
        "--range. The Meta tab's date window. Re-applied for every champion,\n"
        "because the app forgets it each time one is opened.\n"
        "'any' needs a subscription. 'none' leaves the app's own 28-day window.\n"
        "Shorter windows mean smaller matchup samples.\n\n"
        "Example:  7d",
    "phase":
        "--phase. Which grid scroll position to walk.\n"
        "'bottom' reaches the last rows without re-walking the first ones, which\n"
        "is what you want when a run stopped short of the full roster.\n\n"
        "Example:  both",
    "scroll_method":
        "--scroll-method. The gesture used to scroll inside the app.\n"
        "'pagekeys' is the one that works: input swipe does not move Flutter's\n"
        "scroll view under BlueStacks at all. Blank means autodetect, which\n"
        "tries each gesture until one visibly moves content.\n\n"
        "Example:  pagekeys",
    "limit":
        "--limit. Stop after this many champions.\n"
        "Always run a small one first and check the positions and the\n"
        "'date range applied:' line before committing three hours.\n\n"
        "Example:  3",
    "port":
        "--port. ADB port of the emulator. Blank autodetects, which is usually\n"
        "right; set it if the run reports 'No adb device'.\n\n"
        "Example:  5555",
    "resume":
        "--resume. Skip champions already captured in this folder - but only when\n"
        "what is on disk covers what this run wants, so a --no-meta pass followed\n"
        "by a full pass correctly re-visits everything.\n\n"
        "Leave it on for a repeat run; turn it off to walk the whole roster fresh.",
    "no_meta":
        "--no-meta. Skip the Meta tab entirely: abilities, base stats and\n"
        "portraits only, about 15 minutes instead of three hours.\n\n"
        "Follow it later with a full run, or fill the gap with 'Merge from'.",
    "no_roles":
        "--no-roles. Capture only the position the app opens on, instead of every\n"
        "position a champion plays. Faster, and loses the per-position win rates,\n"
        "KDA and matchups that are most of the point.",
    "extra":
        "Anything else esmo_capture.py accepts, passed through verbatim.\n"
        "Quote paths containing spaces.\n\n"
        "Examples:  --redo Brewer,Nomad\n"
        "           --pull-apk\n"
        "           --adb \"C:\\platform-tools\\adb.exe\"",
    "merge":
        "--merge. Fill positions and meta from an existing champions.json, for\n"
        "champions this capture took without meta. The follow-up to a --no-meta\n"
        "run: capture the fast half now, borrow last week's meta for the rest.\n\n"
        "Example:  20260820_champions.json",
    "strict_period":
        "--strict-period. Drop any position whose date window differs from the\n"
        "majority, so one file never mixes two capture dates.\n\n"
        "Turn it on when the parser reports MIXED WINDOWS.",
    "parse_extra":
        "Anything else parse_esmo.py accepts, passed through verbatim.\n\n"
        "Example:  --dir other-capture-folder",
    "out_dir":
        "Where the parsed JSON lands. Blank means the capture folder above.\n\n"
        "Example:  D:\\captures\\history",
    "pattern":
        "Name of the parsed file. Placeholders: {date} {preset} {range}.\n"
        "A typo shows up in the preview below rather than three hours from now.\n\n"
        "Examples:  {date}_champions.json   ->  20260827_champions.json\n"
        "           {date}_{range}.json     ->  20260827_7d.json\n"
        "           patch-3.7.json          ->  patch-3.7.json",
    "preview":
        "Exactly what will be written. The run is launched with this literal\n"
        "path, so this is the filename, not a guess at it.",
    "launch":
        "Capture, then parse, in a new console window.\n"
        "Ctrl+C in that console stops a capture; tick --resume to pick it up\n"
        "where it left off. This window can be closed while the run continues.",
    "parse_only":
        "Re-parse the capture folder without capturing anything. Takes seconds\n"
        "and is safe to repeat, which is the whole reason capture and parse are\n"
        "separate steps.",
    "show":
        "Print the esmo.py command this window would run, without running it.\n"
        "The same command works from a terminal, unchanged.",
}


class Tip:
    """Hover text. tkinter ships no tooltip widget, and idlelib's is explicitly not a
    public API, so this is the fifteen lines it takes to depend on neither."""

    def __init__(self, widget, text, delay=400):
        self.widget, self.text, self.delay = widget, text, delay
        self.win = self.pending = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _=None):
        self.cancel()
        self.pending = self.widget.after(self.delay, self.show)

    def cancel(self):
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending = None

    def show(self):
        if self.win:
            return
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry("+%d+%d" % (self.widget.winfo_rootx() + 18,
                                         self.widget.winfo_rooty()
                                         + self.widget.winfo_height() + 4))
        tk.Label(self.win, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, padx=7, pady=5).pack()

    def hide(self, _=None):
        self.cancel()
        if self.win:
            self.win.destroy()
            self.win = None


class App:
    def __init__(self, root):
        self.root = root
        root.title("ESMO capture")
        self.cfg = esmo.load_config()
        state = self.cfg.get("state") or {}

        self.var = {f: tk.StringVar(value=str(state.get(f, ""))) for f in FIELDS}
        self.var.update({c: tk.BooleanVar(value=bool(state.get(c))) for c in CHECKS})
        self.workdir = tk.StringVar(value=state.get("workdir") or str(pathlib.Path.cwd()))
        self.out_dir = tk.StringVar(value=state.get("out_dir", ""))
        self.pattern = tk.StringVar(value=state.get("pattern") or esmo.DEFAULT_PATTERN)
        self.preset = tk.StringVar(value=state.get("preset", ""))
        self.preview = tk.StringVar()

        self._layout()
        for v in list(self.var.values()) + [self.pattern, self.out_dir, self.workdir]:
            v.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    # ------------------------------------------------------------ layout
    def _row(self, f, row, label, widget, key, span=2):
        """One label + control, both carrying the same hover text."""
        lab = ttk.Label(f, text=label)
        lab.grid(row=row, column=0, **PAD)
        widget.grid(row=row, column=1, columnspan=span, **PAD)
        Tip(lab, TIPS[key])
        Tip(widget, TIPS[key])
        return row + 1

    def _layout(self):
        f = ttk.Frame(self.root, padding=10)
        f.grid(sticky="nsew")
        row = 0

        row = self._row(f, row, "Capture folder",
                        ttk.Entry(f, textvariable=self.workdir, width=44), "workdir")
        browse = ttk.Button(f, text="Browse", command=self.pick_workdir)
        browse.grid(row=row - 1, column=3, **PAD)
        Tip(browse, TIPS["workdir"])
        hint = ttk.Label(f, text="esmo_capture/ is created inside it", foreground="grey")
        hint.grid(row=row, column=1, columnspan=2, **PAD)
        Tip(hint, TIPS["workdir"])
        row += 1

        presets = esmo.load_presets(self.cfg)
        box = ttk.Combobox(f, textvariable=self.preset, values=list(presets), width=18)
        box.bind("<<ComboboxSelected>>", self.load_preset)
        row = self._row(f, row, "Preset", box, "preset", span=1)
        for col, (text, cmd) in enumerate((("Load", self.load_preset),
                                           ("Save as...", self.save_preset)), start=2):
            b = ttk.Button(f, text=text, command=cmd)
            b.grid(row=row - 1, column=col, **PAD)
            Tip(b, TIPS["preset"])

        row = self._separator(f, row)

        for label, field, values in (("Date range", "range", RANGES),
                                     ("Grid phase", "phase", PHASES),
                                     ("Scroll method", "scroll_method", SCROLLS)):
            row = self._row(f, row, label,
                            ttk.Combobox(f, textvariable=self.var[field], values=values,
                                         width=18), field, span=1)

        for label, field in (("Limit", "limit"), ("ADB port", "port")):
            row = self._row(f, row, label,
                            ttk.Entry(f, textvariable=self.var[field], width=20), field, span=1)

        checks = ttk.Frame(f)
        checks.grid(row=row, column=0, columnspan=4, sticky="w", padx=6)
        for i, (label, field) in enumerate((("--resume", "resume"), ("--no-meta", "no_meta"),
                                            ("--no-roles", "no_roles"))):
            c = ttk.Checkbutton(checks, text=label, variable=self.var[field])
            c.grid(row=0, column=i, padx=6)
            Tip(c, TIPS[field])
        row += 1

        row = self._row(f, row, "Extra capture flags",
                        ttk.Entry(f, textvariable=self.var["extra"], width=44), "extra", span=3)

        row = self._separator(f, row)

        row = self._row(f, row, "Merge from",
                        ttk.Entry(f, textvariable=self.var["merge"], width=44), "merge")
        browse = ttk.Button(f, text="Browse", command=self.pick_merge)
        browse.grid(row=row - 1, column=3, **PAD)
        Tip(browse, TIPS["merge"])

        strict = ttk.Checkbutton(f, text="--strict-period", variable=self.var["strict_period"])
        strict.grid(row=row, column=1, **PAD)
        Tip(strict, TIPS["strict_period"])
        row += 1

        row = self._row(f, row, "Extra parse flags",
                        ttk.Entry(f, textvariable=self.var["parse_extra"], width=44),
                        "parse_extra", span=3)

        row = self._separator(f, row)

        row = self._row(f, row, "Output folder",
                        ttk.Entry(f, textvariable=self.out_dir, width=44), "out_dir")
        browse = ttk.Button(f, text="Browse", command=self.pick_outdir)
        browse.grid(row=row - 1, column=3, **PAD)
        Tip(browse, TIPS["out_dir"])

        row = self._row(f, row, "Filename",
                        ttk.Entry(f, textvariable=self.pattern, width=44), "pattern", span=3)

        preview = ttk.Label(f, textvariable=self.preview, foreground="grey")
        preview.grid(row=row, column=1, columnspan=3, **PAD)
        Tip(preview, TIPS["preview"])
        row += 1

        buttons = ttk.Frame(f)
        buttons.grid(row=row, column=0, columnspan=4, pady=(12, 0))
        for col, (text, cmd, key) in enumerate((("Launch", self.launch, "launch"),
                                                ("Parse only", self.parse_only, "parse_only"),
                                                ("Show command", self.show_command, "show"))):
            b = ttk.Button(buttons, text=text, command=cmd)
            b.grid(row=0, column=col, padx=6)
            Tip(b, TIPS[key])

    def _separator(self, f, row):
        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=4,
                                                   sticky="ew", pady=8)
        return row + 1

    # ------------------------------------------------------------ state
    def settings(self):
        return {k: (v.get() if isinstance(v, tk.StringVar) else bool(v.get()))
                for k, v in self.var.items()}

    def apply(self, s):
        for k, v in self.var.items():
            if isinstance(v, tk.StringVar):
                v.set(str(s.get(k, "")))
            else:
                v.set(bool(s.get(k)))

    def out_path(self):
        """Where this run's JSON will land. The launch passes this as a literal --out, so
        the preview below is not an estimate of the filename - it is the filename."""
        cap, _ = esmo.settings_to_args(self.settings())
        name = esmo.format_name(self.pattern.get(),
                                datetime.date.today().strftime("%Y%m%d"),
                                self.preset.get() or None, esmo.resolve_range(cap))
        return pathlib.Path(self.out_dir.get() or self.workdir.get() or ".") / name

    def refresh(self):
        """Live filename preview. It doubles as the placeholder check: a typo shows up
        here rather than at the end of a three-hour run."""
        try:
            self.preview.set(str(self.out_path()))
        except (KeyError, IndexError, ValueError) as e:
            self.preview.set(f"bad placeholder: {e}")

    def load_preset(self, *_):
        name = self.preset.get()
        presets = esmo.load_presets(esmo.load_config())
        if name not in presets:
            return
        p = presets[name]
        self.apply(esmo.args_to_settings(p["capture"], p["parse"]))

    def save_preset(self):
        name = simpledialog.askstring("Save preset", "Preset name:", parent=self.root)
        if not name:
            return
        cap, par = esmo.settings_to_args(self.settings())
        cfg = esmo.load_config()
        cfg.setdefault("presets", {})[name] = {
            "capture": cap, "parse": par, "description": "saved from the GUI",
        }
        esmo.save_config(cfg)
        self.cfg = cfg
        self.preset.set(name)
        messagebox.showinfo("Saved", f"{name} written to {esmo.CONFIG}")

    def remember(self):
        cfg = esmo.load_config()
        state = self.settings()
        state.update(workdir=self.workdir.get(), out_dir=self.out_dir.get(),
                     pattern=self.pattern.get(), preset=self.preset.get())
        cfg["state"] = state
        esmo.save_config(cfg)

    # ------------------------------------------------------------ pickers
    def pick_workdir(self):
        d = filedialog.askdirectory(initialdir=self.workdir.get() or ".")
        if d:
            self.workdir.set(d)

    def pick_outdir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get() or self.workdir.get() or ".")
        if d:
            self.out_dir.set(d)

    def pick_merge(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if p:
            self.var["merge"].set(p)

    # ------------------------------------------------------------ launching
    def command(self, parse_only=False):
        """The esmo.py invocation this window represents. esmo.py does the sequencing,
        the stop-on-failure and the console hold; this only assembles its argv.

        The preset name is deliberately *not* passed. Loading a preset fills the widgets,
        and from that moment the widgets are the truth - naming the preset as well would
        re-add flags you had just unticked, since esmo.py puts preset flags first."""
        cap, par = esmo.settings_to_args(self.settings())
        cmd = [sys.executable, str(esmo.HERE / "esmo.py"),
               "--out", str(self.out_path()), "--hold"]
        if parse_only:
            cmd.append("--parse-only")
        cmd += cap
        if par:
            cmd += ["--", *par]
        return cmd

    def spawn(self, parse_only=False):
        workdir = self.workdir.get() or str(pathlib.Path.cwd())
        if not pathlib.Path(workdir).is_dir():
            messagebox.showerror("No such folder", workdir)
            return
        if "bad placeholder" in self.preview.get():
            messagebox.showerror("Filename", self.preview.get())
            return
        self.remember()
        subprocess.Popen(self.command(parse_only), cwd=workdir, creationflags=NEW_CONSOLE)

    def launch(self):
        self.spawn(False)

    def parse_only(self):
        self.spawn(True)

    def show_command(self):
        messagebox.showinfo("Command", " ".join(self.command()))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
