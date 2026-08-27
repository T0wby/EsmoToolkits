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
    def _layout(self):
        pad = dict(padx=6, pady=3, sticky="w")
        f = ttk.Frame(self.root, padding=10)
        f.grid(sticky="nsew")
        row = 0

        ttk.Label(f, text="Capture folder").grid(row=row, column=0, **pad)
        ttk.Entry(f, textvariable=self.workdir, width=44).grid(row=row, column=1, columnspan=2, **pad)
        ttk.Button(f, text="Browse", command=self.pick_workdir).grid(row=row, column=3, **pad)
        row += 1
        ttk.Label(f, text="esmo_capture/ is created inside it", foreground="grey").grid(
            row=row, column=1, columnspan=2, **pad)
        row += 1

        presets = esmo.load_presets(self.cfg)
        ttk.Label(f, text="Preset").grid(row=row, column=0, **pad)
        box = ttk.Combobox(f, textvariable=self.preset, values=list(presets), width=18)
        box.grid(row=row, column=1, **pad)
        box.bind("<<ComboboxSelected>>", self.load_preset)
        ttk.Button(f, text="Load", command=self.load_preset).grid(row=row, column=2, **pad)
        ttk.Button(f, text="Save as...", command=self.save_preset).grid(row=row, column=3, **pad)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=4,
                                                   sticky="ew", pady=8)
        row += 1

        for label, field, values in (("Date range", "range", RANGES),
                                     ("Grid phase", "phase", PHASES),
                                     ("Scroll method", "scroll_method", SCROLLS)):
            ttk.Label(f, text=label).grid(row=row, column=0, **pad)
            ttk.Combobox(f, textvariable=self.var[field], values=values,
                         width=18).grid(row=row, column=1, **pad)
            row += 1

        for label, field in (("Limit", "limit"), ("ADB port", "port")):
            ttk.Label(f, text=label).grid(row=row, column=0, **pad)
            ttk.Entry(f, textvariable=self.var[field], width=20).grid(row=row, column=1, **pad)
            row += 1

        checks = ttk.Frame(f)
        checks.grid(row=row, column=0, columnspan=4, sticky="w", padx=6)
        for i, (label, field) in enumerate((("--resume", "resume"), ("--no-meta", "no_meta"),
                                            ("--no-roles", "no_roles"))):
            ttk.Checkbutton(checks, text=label, variable=self.var[field]).grid(
                row=0, column=i, padx=6)
        row += 1

        ttk.Label(f, text="Extra capture flags").grid(row=row, column=0, **pad)
        ttk.Entry(f, textvariable=self.var["extra"], width=44).grid(
            row=row, column=1, columnspan=3, **pad)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=4,
                                                   sticky="ew", pady=8)
        row += 1

        ttk.Label(f, text="Merge from").grid(row=row, column=0, **pad)
        ttk.Entry(f, textvariable=self.var["merge"], width=44).grid(
            row=row, column=1, columnspan=2, **pad)
        ttk.Button(f, text="Browse", command=self.pick_merge).grid(row=row, column=3, **pad)
        row += 1
        ttk.Checkbutton(f, text="--strict-period", variable=self.var["strict_period"]).grid(
            row=row, column=1, **pad)
        row += 1
        ttk.Label(f, text="Extra parse flags").grid(row=row, column=0, **pad)
        ttk.Entry(f, textvariable=self.var["parse_extra"], width=44).grid(
            row=row, column=1, columnspan=3, **pad)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=4,
                                                   sticky="ew", pady=8)
        row += 1

        ttk.Label(f, text="Output folder").grid(row=row, column=0, **pad)
        ttk.Entry(f, textvariable=self.out_dir, width=44).grid(row=row, column=1, columnspan=2, **pad)
        ttk.Button(f, text="Browse", command=self.pick_outdir).grid(row=row, column=3, **pad)
        row += 1
        ttk.Label(f, text="Filename").grid(row=row, column=0, **pad)
        ttk.Entry(f, textvariable=self.pattern, width=44).grid(row=row, column=1, columnspan=3, **pad)
        row += 1
        ttk.Label(f, textvariable=self.preview, foreground="grey").grid(
            row=row, column=1, columnspan=3, **pad)
        row += 1

        buttons = ttk.Frame(f)
        buttons.grid(row=row, column=0, columnspan=4, pady=(12, 0))
        ttk.Button(buttons, text="Launch", command=self.launch).grid(row=0, column=0, padx=6)
        ttk.Button(buttons, text="Parse only", command=self.parse_only).grid(row=0, column=1, padx=6)
        ttk.Button(buttons, text="Show command", command=self.show_command).grid(
            row=0, column=2, padx=6)

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
