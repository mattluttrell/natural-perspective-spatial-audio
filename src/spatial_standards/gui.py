"""spatial-standards-gui — the basic GUI over the same pipeline as the CLI.

Pick file(s), a folder, or paste a URL → pick standard → Optimized on/off →
output folder → Go. Outputs Plex-friendly tagged 7.1 FLACs in the
<Artist>/<Album>/<Title> layout.

Inputs may also be passed as command-line parameters:
  spatial-standards-gui song.flac /music/album "https://…"

A first-run acknowledgment and a persistent notice cover the input-rights
posture (see NOTICE). External tools are found on PATH or via env vars:
SPATIAL_STANDARDS_FFMPEG / _DEMUCS / _SEPARATOR / _YTDLP.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import RIGHTS_NOTICE, __version__, proc, ytdlp_update
from .envfile import load_env
from .ingest import expand_inputs, has_directory, is_url
from .pipeline import (Bins, SkippedInput, ensure_ffmpeg_on_path, process,
                       process_natural, resolve_bin)
from .system_profile import parse_system_profile

ACK_FILE = Path.home() / ".config" / "spatial-standards" / "rights-acknowledged"
SETTINGS_FILE = Path.home() / ".config" / "spatial-standards" / "settings.json"
USER_ENV_FILE = Path.home() / ".config" / "spatial-standards" / ".env"  # read by envfile.load_env
URL_FIELD_NOTICE = ("By processing a URL or file you affirm you own or have "
                    "rights to this content.")
LOGO = Path(__file__).parent / "assets" / "clearbay-logo.png"

# Clearbay dark-mode palette (--color-navy / --color-navy-light from src/index.css)
BG = "#0A1628"          # --color-navy, page/window background
PANEL = "#112240"       # --color-navy-light, card/header surfaces
FG = "#F0F4F8"          # light text on dark background
DARK = "#F0F4F8"        # wordmark / button text (light on dark)
MUTED = "#8B9DC3"       # muted text — blue-tinted, readable on navy
BORDER = "#1E3A5F"      # hairline borders
BTN_BORDER = "#2A4A7F"  # .cb-btn border
BTN_HOVER = "#0D2847"   # .cb-btn:hover — deeper navy
TEAL = "#0D9488"        # --color-teal (unchanged)
FONT = "Inter"          # --font-sans (falls back if not installed)


def _elapsed(t0: float) -> str:
    secs = int(time.monotonic() - t0)
    return f"{secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{secs}s"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def compute_scale(root: tk.Tk) -> float:
    """UI scale for HiDPI displays. Tk does not scale itself — derive a
    factor from the X-reported DPI or the screen height (4K ≈ 2.0), with
    SPATIAL_STANDARDS_UI_SCALE as the manual override."""
    env = os.environ.get("SPATIAL_STANDARDS_UI_SCALE")
    if env:
        return max(1.0, min(float(env), 4.0))
    dpi = root.winfo_fpixels("1i")
    h = root.winfo_screenheight()
    scale = max(dpi / 96.0, h / 1080.0 if h >= 1600 else 1.0)
    return max(1.0, min(scale, 3.0))


def _font_family() -> str:
    return FONT if FONT in tkfont.families() else "sans-serif"


def apply_theme(root: tk.Tk, scale: float = 1.0) -> None:
    root.configure(bg=BG)
    root.tk.call("tk", "scaling", scale * 1.33)
    family = _font_family()
    # Negative Tk font sizes are pixels — scale every named font.
    for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont",
                 "TkHeadingFont", "TkCaptionFont", "TkTooltipFont", "TkIconFont"):
        try:
            f = tkfont.nametofont(name)
            f.configure(size=-round(14 * scale))
            if name != "TkFixedFont":
                f.configure(family=family)
        except tk.TclError:
            pass
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                    bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                    font=(family, -round(14 * scale)))
    # White cards with the site's border color
    style.configure("TLabelframe", background=PANEL, bordercolor=BORDER,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("Card.TLabel", background=PANEL)
    style.configure("Header.TFrame", background=PANEL)
    # .cb-btn: white, 1px #d1d1d6 border, dark text, square, hover #d1d1d6
    style.configure("TButton", background=PANEL, foreground=DARK,
                    bordercolor=BTN_BORDER, borderwidth=1, relief="solid",
                    focuscolor=PANEL, padding=(round(14 * scale), round(8 * scale)))
    style.map("TButton", background=[("active", BTN_HOVER), ("disabled", BG)],
              foreground=[("disabled", MUTED)])
    style.configure("TEntry", insertcolor=FG, bordercolor=BORDER,
                    fieldbackground=PANEL, foreground=FG)
    style.configure("TRadiobutton", background=PANEL, foreground=FG)
    style.configure("TCheckbutton", background=PANEL, foreground=FG)
    style.map("TRadiobutton", background=[("active", PANEL)])
    style.map("TCheckbutton", background=[("active", PANEL)])
    style.configure("Muted.TLabel", foreground=MUTED, background=PANEL)
    # clearbay | ai wordmark pieces (.logo-text / .logo-pipe / .logo-accent)
    wordmark = (family, -round(20 * scale), "bold")
    style.configure("WordDark.TLabel", foreground=DARK, background=PANEL, font=wordmark)
    style.configure("WordPipe.TLabel", foreground="#2D4B6E", background=PANEL,
                    font=(family, -round(20 * scale)))
    style.configure("WordTeal.TLabel", foreground=TEAL, background=PANEL, font=wordmark)
    style.configure("Title.TLabel", foreground=FG, background=PANEL,
                    font=(family, -round(15 * scale)))


def bins_from_env() -> Bins:
    # An explicit env override wins; otherwise resolve from PATH or the venv.
    return Bins(
        ffmpeg=os.environ.get("SPATIAL_STANDARDS_FFMPEG") or resolve_bin("ffmpeg"),
        demucs=os.environ.get("SPATIAL_STANDARDS_DEMUCS") or resolve_bin("demucs"),
        separator=os.environ.get("SPATIAL_STANDARDS_SEPARATOR") or resolve_bin("audio-separator"),
        ytdlp=os.environ.get("SPATIAL_STANDARDS_YTDLP") or resolve_bin("yt-dlp"),
    )


class App:
    def __init__(self, root: tk.Tk, initial_inputs: list[str], scale: float = 1.0):
        self.root = root
        s = scale
        self._s = scale
        root.title(f"Natural Perspective Spatial Audio v{__version__}")
        root.minsize(round(640 * s), round(520 * s))

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_browse_dir: str | None = None

        pad = {"padx": round(8 * s), "pady": round(4 * s)}

        # --- Site header: white bar, bottom border, "clearbay | ai" wordmark ---
        header = ttk.Frame(root, style="Header.TFrame")
        header.pack(fill="x")
        inner = ttk.Frame(header, style="Header.TFrame")
        inner.pack(fill="x", padx=round(16 * s), pady=round(14 * s))
        self._logo = None
        if LOGO.exists():
            img = tk.PhotoImage(file=str(LOGO))
            root.iconphoto(True, img)
        ttk.Label(inner, text="clearbay", style="WordDark.TLabel").pack(side="left")
        ttk.Label(inner, text=" | ", style="WordPipe.TLabel").pack(side="left")
        ttk.Label(inner, text="ai", style="WordTeal.TLabel").pack(side="left")
        ttk.Label(inner, text="Natural Perspective", style="Title.TLabel").pack(side="right")
        tk.Frame(header, height=1, bg=BORDER).pack(fill="x")

        # --- Inputs ---
        frm_in = ttk.LabelFrame(root, text="Inputs — audio files, folders, or URLs")
        frm_in.pack(fill="both", expand=False, **pad)

        row = ttk.Frame(frm_in, style="Card.TFrame")
        row.pack(fill="x", **pad)
        self.entry = ttk.Entry(row)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.add_entry())
        ttk.Button(row, text="Add", command=self.add_entry).pack(side="left", padx=4)
        ttk.Button(row, text="Files…", command=self.add_files).pack(side="left")
        ttk.Button(row, text="Folder…", command=self.add_folder).pack(side="left", padx=4)

        ttk.Label(frm_in, text=URL_FIELD_NOTICE, style="Muted.TLabel",
                  wraplength=round(600 * s)).pack(fill="x", padx=round(8 * s))

        self.listbox = tk.Listbox(frm_in, height=6, selectmode="extended",
                                  bg=PANEL, fg=FG, selectbackground=TEAL,
                                  selectforeground=PANEL,
                                  highlightthickness=1, highlightbackground=BORDER,
                                  highlightcolor=BORDER, borderwidth=0)
        self.listbox.pack(fill="both", expand=True, **pad)
        self.listbox.bind("<Delete>", lambda e: self.remove_selected())
        self.listbox.bind("<BackSpace>", lambda e: self.remove_selected())

        botrow = ttk.Frame(frm_in, style="Card.TFrame")
        botrow.pack(fill="x", padx=round(8 * s), pady=(0, round(6 * s)))
        self.recursive = tk.BooleanVar(value=True)
        self.recursive_chk = ttk.Checkbutton(
            botrow, text="Include sub-folders (recursive)", variable=self.recursive)
        # shown only when a folder is in the queue; packed/forgotten dynamically
        ttk.Button(botrow, text="Remove selected",
                   command=self.remove_selected).pack(side="right")
        ttk.Button(botrow, text="Select all",
                   command=self.select_all).pack(side="right", padx=4)

        # --- Options ---
        frm_opt = ttk.LabelFrame(root, text="Natural Perspective")
        frm_opt.pack(fill="x", **pad)
        # Natural Perspective is the only GUI mode; legacy fixed mixes live in the CLI.
        self.standard = tk.StringVar(value="natural")
        self.optimized = tk.BooleanVar(value=False)  # decided per track by the config
        self.key_label = ttk.Label(frm_opt, style="Muted.TLabel", wraplength=round(600 * s))
        self.key_label.pack(anchor="w", padx=8, pady=(6, 2))
        # No key yet? Take it here and save it to the user config .env (which
        # load_env reads), so nobody has to find a dotfile or relaunch.
        self.key_row = ttk.Frame(frm_opt, style="Card.TFrame")
        ttk.Label(self.key_row, text="Anthropic API key:", style="Card.TLabel").pack(side="left")
        self.key_entry = ttk.Entry(self.key_row, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.key_entry.bind("<Return>", lambda e: self.save_api_key())
        ttk.Button(self.key_row, text="Save", command=self.save_api_key).pack(side="left")
        self.refresh_key_state()

        self.keep_video = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_opt, text="Output video (MKV) — keep the picture for "
                                      "video files and URLs",
                        variable=self.keep_video).pack(anchor="w", padx=8, pady=(0, 2))

        # Optional comments.md — notes for the model + per-channel rig trims.
        sysrow = ttk.Frame(frm_opt, style="Card.TFrame")
        sysrow.pack(fill="x", padx=round(24 * s), pady=(0, 6))
        self.sys_label = ttk.Label(sysrow, text="Comments (type notes, or pick a file):",
                                   style="Card.TLabel")
        self.sys_label.pack(side="left")
        self.system_file = tk.StringVar(value="")
        self.sys_entry = ttk.Entry(sysrow, textvariable=self.system_file)
        self.sys_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.sys_browse = ttk.Button(sysrow, text="Browse…", command=self.browse_system)
        self.sys_browse.pack(side="left")
        self.sys_clear = ttk.Button(sysrow, text="Clear",
                                    command=lambda: self.system_file.set(""))
        self.sys_clear.pack(side="left", padx=4)

        # --- Output ---
        frm_out = ttk.LabelFrame(root, text="Output library folder")
        frm_out.pack(fill="x", **pad)
        row2 = ttk.Frame(frm_out, style="Card.TFrame")
        row2.pack(fill="x", **pad)
        self.out_dir = tk.StringVar(value=str(Path.home() / "spatial-audio"))
        ttk.Entry(row2, textvariable=self.out_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="Browse…", command=self.pick_out).pack(side="left", padx=4)

        # --- Go / Cancel + log ---
        gorow = ttk.Frame(root)
        gorow.pack(fill="x", **pad)
        self.go_btn = ttk.Button(gorow, text="Go", command=self.start)
        self.go_btn.pack(side="left", expand=True)
        self.cancel_btn = ttk.Button(gorow, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        # yt-dlp is the one tool that goes stale (YouTube changes; a pip-installed
        # yt-dlp can't update itself). Updates also happen automatically before
        # a download; this is the manual button for "YouTube stopped working".
        ttk.Button(gorow, text="Update yt-dlp", command=self.update_ytdlp).pack(side="right")
        ttk.Button(gorow, text="Open output folder", command=self.open_out_dir).pack(
            side="right", padx=4)

        # Which tools resolved, and which yt-dlp — the first thing to look at
        # when a run fails. Filled in off the UI thread (yt-dlp --version).
        self.status = ttk.Label(root, text="checking tools…", style="Muted.TLabel")
        self.status.pack(side="bottom", anchor="w", padx=round(10 * s), pady=(0, round(6 * s)))

        self.log = tk.Text(root, height=10, state="disabled",
                           bg=PANEL, fg=MUTED, insertbackground=FG,
                           highlightthickness=1, highlightbackground=BORDER,
                           highlightcolor=BORDER, borderwidth=0)
        self.log.pack(fill="both", expand=True, **pad)
        # Progress lines ("separating … 45% · about 1:02 left") overwrite the
        # step's previous line instead of stacking up.
        self._last_msg: str | None = None
        self._last_start: str | None = None

        # Restore last session, then layer any command-line inputs on top.
        prefs = load_settings()
        # Natural Perspective is the only GUI mode — ignore any stale saved
        # "standard" (e.g. onstage/frontrow from an older version).
        self.standard.set("natural")
        self.optimized.set(prefs.get("optimized", False))
        self.keep_video.set(prefs.get("keep_video", False))
        self.system_file.set(prefs.get("system_file", ""))
        if prefs.get("out_dir"):
            self.out_dir.set(prefs["out_dir"])
        for src in prefs.get("inputs", []):
            self.listbox.insert("end", src)
        if prefs.get("geometry"):
            try:
                root.geometry(prefs["geometry"])
            except tk.TclError:
                pass
        for src in initial_inputs:
            if src not in self.listbox.get(0, "end"):
                self.listbox.insert("end", src)
        self.recursive.set(prefs.get("recursive", True))
        self.last_browse_dir = prefs.get("last_browse_dir")
        self.refresh_recursive_visibility()
        self.refresh_system_visibility()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.drain_log)
        threading.Thread(target=self.refresh_status, daemon=True).start()

    def save_prefs(self):
        save_settings({
            "standard": self.standard.get(),
            "optimized": self.optimized.get(),
            "keep_video": self.keep_video.get(),
            "system_file": self.system_file.get(),
            "recursive": self.recursive.get(),
            "last_browse_dir": self.last_browse_dir,
            "out_dir": self.out_dir.get(),
            "inputs": list(self.listbox.get(0, "end")),
            "geometry": self.root.geometry(),
        })

    def on_close(self):
        self.save_prefs()
        proc.cancel()  # don't leave a Demucs running headless after the window is gone
        self.root.destroy()

    # -- input handling --
    def refresh_recursive_visibility(self):
        """Show the recursive checkbox only when a folder is queued."""
        if has_directory(list(self.listbox.get(0, "end"))):
            if not self.recursive_chk.winfo_ismapped():
                self.recursive_chk.pack(side="left")
        elif self.recursive_chk.winfo_ismapped():
            self.recursive_chk.pack_forget()

    def add_entry(self):
        s = self.entry.get().strip()
        if s:
            self.listbox.insert("end", s)
            self.entry.delete(0, "end")
            self.refresh_recursive_visibility()

    def add_files(self):
        files = filedialog.askopenfilenames(title="Choose audio files",
                                            initialdir=self.last_browse_dir or None)
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.last_browse_dir = str(Path(files[-1]).parent)
        self.refresh_recursive_visibility()

    def add_folder(self):
        d = filedialog.askdirectory(title="Choose a folder or audio",
                                    initialdir=self.last_browse_dir or None)
        if d:
            self.listbox.insert("end", d)
            self.last_browse_dir = str(Path(d).parent)
            self.refresh_recursive_visibility()

    def select_all(self):
        self.listbox.selection_set(0, "end")
        self.listbox.focus_set()

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
        self.refresh_recursive_visibility()

    def open_out_dir(self):
        d = Path(self.out_dir.get()).expanduser()
        if not d.is_dir():
            self.log_line(f"output folder does not exist yet: {d}")
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(d)])
        elif os.name == "nt":
            os.startfile(str(d))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(d)])

    def refresh_key_state(self):
        have = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.key_label.configure(
            text="A model designs the spatial mix for each recording. "
                 + ("API key detected — the model designs each mix." if have else
                    "No API key found — the default mix is used until you add one.")
                 + " Video files come out as MKV with the picture kept.")
        if have:
            self.key_row.pack_forget()
        else:
            self.key_row.pack(fill="x", padx=round(24 * self._s), pady=(0, 6),
                              after=self.key_label)

    def save_api_key(self):
        key = self.key_entry.get().strip()
        if not key:
            return
        try:
            USER_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            lines = [ln for ln in (USER_ENV_FILE.read_text().splitlines()
                                   if USER_ENV_FILE.exists() else [])
                     if not ln.strip().startswith("ANTHROPIC_API_KEY=")]
            lines.append(f"ANTHROPIC_API_KEY={key}")
            USER_ENV_FILE.write_text("\n".join(lines) + "\n")
            try:
                USER_ENV_FILE.chmod(0o600)
            except OSError:
                pass
        except OSError as e:
            self.log_line(f"could not save the key to {USER_ENV_FILE}: {e}")
            return
        os.environ["ANTHROPIC_API_KEY"] = key  # the client reads it per call, no relaunch
        self.key_entry.delete(0, "end")
        self.log_line(f"API key saved to {USER_ENV_FILE} — Natural Perspective is on.")
        self.refresh_key_state()

    def refresh_status(self):
        """Tool status line: '✓ ffmpeg · ✓ demucs · ✗ audio-separator · yt-dlp 2026.08.19'."""
        bins = bins_from_env()
        ensure_ffmpeg_on_path(bins.ffmpeg)
        parts = []
        for name, path in (("ffmpeg", bins.ffmpeg), ("demucs", bins.demucs),
                           ("audio-separator", bins.separator)):
            parts.append(("✓ " if shutil.which(path) else "✗ ") + name)
        v = ytdlp_update.installed_version(bins.ytdlp) if shutil.which(bins.ytdlp) else None
        parts.append(f"yt-dlp {v}" if v else "✗ yt-dlp")
        text = "  ·  ".join(parts)
        if any(p.startswith("✗") for p in parts):
            text += "   — a ✗ tool is not installed or not on PATH (see README)"
        self.root.after(0, lambda: self.status.configure(text=text))

    def pick_out(self):
        d = filedialog.askdirectory(title="Output library folder",
                                    initialdir=self.out_dir.get() or None)
        if d:
            self.out_dir.set(d)

    def refresh_system_visibility(self):
        """The speaker profile feeds the Optimized pass (and Natural
        Perspective, which may turn Optimized on) — enable its row only then."""
        on = self.optimized.get() or self.standard.get() == "natural"
        state = ["!disabled"] if on else ["disabled"]
        for w in (self.sys_entry, self.sys_browse, self.sys_clear):
            w.state(state)
        self.sys_label.configure(foreground=FG if on else MUTED)

    def browse_system(self):
        f = filedialog.askopenfilename(
            title="Comments file (comments.md)",
            initialdir=self.last_browse_dir or None,
            filetypes=[("Markdown / text", "*.md *.markdown *.txt"), ("All files", "*.*")])
        if f:
            self.system_file.set(f)

    # -- processing --
    def log_line(self, msg: str):
        self.log_queue.put(msg)

    def drain_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                if proc.replaces(self._last_msg, msg) and self._last_start:
                    self.log.delete(self._last_start, "end-1c")  # overwrite the step's line
                else:
                    self._last_start = self.log.index("end-1c")
                self._last_msg = msg
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self.drain_log)

    def _set_running(self, running: bool):
        self.go_btn.configure(state="disabled" if running else "normal",
                              text="Working…" if running else "Go")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    def cancel(self):
        self.cancel_btn.configure(state="disabled")
        self.log_line("cancelling — stopping the current step…")
        proc.cancel()

    def update_ytdlp(self):
        """Manual yt-dlp update (the automatic one runs before each download)."""
        if self.worker and self.worker.is_alive():
            self.log_line("busy — update yt-dlp after the current batch finishes.")
            return
        bins = bins_from_env()

        def work():
            if ytdlp_update.managed(bins.ytdlp):
                ytdlp_update.upgrade(bins.ytdlp, self.log_line)
            else:
                v = ytdlp_update.installed_version(bins.ytdlp) or "not found"
                self.log_line(f"yt-dlp ({v}) at {bins.ytdlp} was not installed by this app, "
                              f"so it won't be changed. To update it: "
                              f"{ytdlp_update.manual_hint(bins.ytdlp)}")
            self.root.after(0, lambda: self._set_running(False))

        self._set_running(True)
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        sources = list(self.listbox.get(0, "end"))
        if not sources:
            messagebox.showwarning("Natural Perspective Spatial Audio",
                                   "Add at least one file, folder, or URL.")
            return
        # Inputs are expanded (folders → files, playlist URLs → each video) in the
        # worker, so a big playlist lookup doesn't freeze the UI on Go.
        std = self.standard.get()
        opt = self.optimized.get()
        out = Path(self.out_dir.get()).expanduser()
        comments_path = self.system_file.get().strip() or None

        # Legacy onstage/frontrow parse the rig trims up front; Natural
        # Perspective reads the whole comments file in the worker.
        profile = None
        if comments_path and std != "natural" and opt:
            try:
                profile = parse_system_profile(Path(comments_path).expanduser())
            except OSError as e:
                messagebox.showerror("Natural Perspective Spatial Audio",
                                     f"Cannot read comments file:\n{e}")
                return

        self.save_prefs()
        proc.reset()
        self._set_running(True)
        self.worker = threading.Thread(
            target=self.run_batch,
            args=(sources, std, opt, out, profile, comments_path,
                  self.keep_video.get(), self.recursive.get()),
            daemon=True)
        self.worker.start()

    def run_batch(self, sources: list[str], standard: str, optimized: bool, out: Path,
                  profile=None, comments_path: str | None = None, want_video: bool = False,
                  recursive: bool = True):
        # Whatever happens in here — including an error nobody anticipated —
        # the buttons must come back, or the window is stuck on "Working…".
        try:
            self._run_batch(sources, standard, optimized, out, profile, comments_path,
                            want_video, recursive)
        except proc.Cancelled:
            self.log_line("Cancelled.")
        except Exception as e:
            self.log_line(f"error: {e}")
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _run_batch(self, sources, standard, optimized, out, profile, comments_path,
                   want_video, recursive):
        bins = bins_from_env()
        ensure_ffmpeg_on_path(bins.ffmpeg, progress=self.log_line)
        # Expand folders → files and playlist URLs → each video (network for
        # playlists), off the UI thread.
        try:
            self.log_line("reading inputs…")
            sources = expand_inputs(sources, recursive=recursive, ytdlp=bins.ytdlp,
                                    progress=self.log_line)
        except (FileNotFoundError, RuntimeError) as e:
            self.log_line(f"error: {e}")
            return
        # The comments field accepts either a file path or typed-in notes.
        comments = comments_text = None
        if comments_path:
            p = Path(comments_path).expanduser()
            if p.is_file():
                comments = p
            else:
                comments_text = comments_path
        failures = skipped = 0
        t_batch = time.monotonic()
        for i, src in enumerate(sources, 1):
            t0 = time.monotonic()
            if proc.cancelled():
                self.log_line(f"Cancelled with {len(sources) - i + 1} input(s) left.")
                break
            self.log_line(f"[{i}/{len(sources)}] {src}")
            try:
                if standard == "natural":
                    dest, sidecar = process_natural(
                        src, out_dir=out, bins=bins, comments=comments,
                        comments_text=comments_text, want_video=want_video,
                        progress=lambda m: self.log_line(f"    {m}"))
                    self.log_line(f"    -> {dest}  ({_elapsed(t0)})")
                    self.log_line(f"    docs -> {sidecar.parent / 'index.html'}")
                else:
                    dest = process(src, standard=standard, optimized=optimized,
                                   out_dir=out, bins=bins, system_profile=profile,
                                   progress=lambda m: self.log_line(f"    {m}"))
                    self.log_line(f"    -> {dest}  ({_elapsed(t0)})")
            except SkippedInput as e:
                skipped += 1
                self.log_line(f"    skipped: {e}")
            except proc.Cancelled:
                self.log_line("    cancelled.")
                break
            except Exception as e:
                failures += 1
                self.log_line(f"    FAILED: {e}")
        else:
            ok = len(sources) - failures - skipped
            self.log_line(f"Finished in {_elapsed(t_batch)}: {ok} ok, {failures} failed, "
                          f"{skipped} skipped.")


def first_run_ack(root: tk.Tk) -> bool:
    """One-time acknowledgment of the input-rights posture."""
    if ACK_FILE.exists():
        return True
    ok = messagebox.askokcancel(
        "Natural Perspective Spatial Audio — before you start",
        RIGHTS_NOTICE + "\n\nThis software is provided AS-IS, without warranty "
        "of any kind (see LICENSE and NOTICE).\n\nClick OK to acknowledge.",
        parent=root)
    if ok:
        ACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACK_FILE.write_text("acknowledged\n")
    return ok


def main(argv: list[str] | None = None) -> int:
    load_env()  # pick up ANTHROPIC_API_KEY etc. from .env if present
    args = sys.argv[1:] if argv is None else argv
    initial = [a for a in args if is_url(a) or Path(a).expanduser().exists()]
    rejected = [a for a in args if a not in initial]

    root = tk.Tk()
    root.withdraw()
    scale = compute_scale(root)
    apply_theme(root, scale)
    if not first_run_ack(root):
        return 1
    root.deiconify()
    app = App(root, initial, scale)
    for r in rejected:
        app.log_line(f"Ignored argument (not a file, folder, or URL): {r}")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
