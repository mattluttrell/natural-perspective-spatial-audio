#!/usr/bin/env python3
"""Does natural-perspective-spatial-audio need a new release?

Meant to run unattended once a month. It answers two questions and writes a
Markdown report (stdout, or --out FILE) whose first line is a headline that
doubles as an email subject:

1. **Is a release pending?** — commits on origin/main since the last tag, the
   unreleased CHANGELOG section, and the version in pyproject vs PyPI.
2. **Does the shipped release still work?** — install the *published* package
   from PyPI into a fresh venv (what a new user gets today, with today's
   dependency versions), run the pipeline on a short clip on CPU, run one
   Natural Perspective design (the model path) when an API key is available,
   and probe yt-dlp against YouTube. Dependencies are unpinned on purpose, so
   this is the only way to know whether a new torch/yt-dlp/anthropic broke a
   fresh install.

Verdicts, in priority order:
  RELEASE NEEDED (fix)   — the smoke test failed: users are broken today.
  RELEASE READY          — unreleased work is sitting on main and the smoke test passed.
  NO RELEASE NEEDED      — nothing unreleased; shipped version still works.

Stdlib only. Uses `git` (fetches origin, never touches the working tree),
`ffmpeg`, and the network (PyPI JSON, GitHub API, YouTube via yt-dlp).
Exit code: 0 when the report was produced, 1 when the script itself failed.

    scripts/release_check.py [--repo DIR] [--out FILE] [--clip FILE]
                             [--skip-smoke] [--keep-venv DIR] [--python EXE]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

PACKAGE = "natural-perspective-spatial-audio"
GITHUB_REPO = "mattluttrell/natural-perspective-spatial-audio"
DEPS = ("yt-dlp", "demucs", "audio-separator", "anthropic", "torch", "torchcodec", "deno")
YT_PROBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 s
CLIP_SECONDS = 30


# ----------------------------------------------------------------------------- helpers

def sh(cmd: list[str], cwd: Path | None = None, timeout: int = 900, env: dict | None = None
       ) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=env)


def fetch_json(url: str, timeout: float = 15) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"{PACKAGE}-release-check"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def days_ago(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return f"{(datetime.now(timezone.utc) - d).days} d ago"
    except ValueError:
        return "?"


def fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


# ----------------------------------------------------------------------------- repo state

def repo_state(repo: Path) -> dict:
    st: dict = {"repo": str(repo)}
    fetched = sh(["git", "fetch", "-q", "origin"], cwd=repo, timeout=120)
    st["fetch_ok"] = fetched.returncode == 0
    st["fetch_err"] = fetched.stderr.strip()
    ref = "origin/main" if st["fetch_ok"] else "HEAD"
    st["ref"] = ref

    tag = sh(["git", "describe", "--tags", "--abbrev=0", ref], cwd=repo)
    st["last_tag"] = tag.stdout.strip() or None
    if st["last_tag"]:
        log = sh(["git", "log", "--format=%s", f"{st['last_tag']}..{ref}"], cwd=repo)
        st["commits"] = [ln for ln in log.stdout.splitlines() if ln.strip()]
        tag_date = sh(["git", "log", "-1", "--format=%cs", st["last_tag"]], cwd=repo)
        st["last_tag_date"] = tag_date.stdout.strip()
    else:
        st["commits"] = []
        st["last_tag_date"] = None

    pyproject = sh(["git", "show", f"{ref}:pyproject.toml"], cwd=repo).stdout
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    st["main_version"] = m.group(1) if m else None

    changelog = sh(["git", "show", f"{ref}:CHANGELOG.md"], cwd=repo).stdout
    m = re.search(r"^## (.+?unreleased.*?)\n(.*?)(?=^## |\Z)", changelog, re.M | re.S | re.I)
    if m:
        st["unreleased_heading"] = m.group(1).strip()
        st["unreleased_bullets"] = [ln for ln in m.group(2).splitlines() if ln.startswith("- ")]
    else:
        st["unreleased_heading"] = None
        st["unreleased_bullets"] = []

    pypi = fetch_json(f"https://pypi.org/pypi/{PACKAGE}/json")
    if pypi:
        st["pypi_version"] = pypi["info"]["version"]
        urls = pypi.get("urls") or []
        st["pypi_uploaded"] = urls[0]["upload_time_iso_8601"] if urls else None
    else:
        st["pypi_version"] = st["pypi_uploaded"] = None

    gh = fetch_json(f"https://api.github.com/repos/{GITHUB_REPO}")
    st["open_issues"] = gh.get("open_issues_count") if gh else None
    st["stars"] = gh.get("stargazers_count") if gh else None
    return st


def dependency_state() -> list[dict]:
    rows = []
    for name in DEPS:
        d = fetch_json(f"https://pypi.org/pypi/{name}/json")
        if not d:
            rows.append({"name": name, "latest": "?", "released": None})
            continue
        urls = d.get("urls") or []
        rows.append({"name": name, "latest": d["info"]["version"],
                     "released": urls[0]["upload_time_iso_8601"] if urls else None})
    return rows


# ----------------------------------------------------------------------------- smoke test

def make_clip(src: Path | None, dest: Path) -> tuple[Path, str]:
    """A ~30 s test clip: trimmed from `src` if given, else synthesized (a
    chord with a beat — enough for the pipeline to run end to end)."""
    if src and src.exists():
        r = sh(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", "30", "-t",
                str(CLIP_SECONDS), "-i", str(src), "-ac", "2", "-ar", "44100", str(dest)])
        if r.returncode == 0:
            return dest, f"first {CLIP_SECONDS}s of {src.name}"
    graph = (f"sine=frequency=110:duration={CLIP_SECONDS}[a];"
             f"sine=frequency=220:duration={CLIP_SECONDS}[b];"
             f"sine=frequency=330:duration={CLIP_SECONDS}[c];"
             f"anoisesrc=color=pink:amplitude=0.05:duration={CLIP_SECONDS}[n];"
             "[a][b][c][n]amix=inputs=4:normalize=0,aformat=channel_layouts=stereo,"
             "volume=0.5")
    r = sh(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", graph,
            "-ar", "44100", str(dest)])
    if r.returncode != 0:
        raise RuntimeError(f"could not make a test clip:\n{r.stderr[-500:]}")
    return dest, "synthesized tones + noise"


def smoke_test(repo: Path, clip_src: Path | None, python: str, keep_venv: Path | None) -> dict:
    """Install the published package into a fresh venv and run it. Returns a
    dict of named steps -> (ok, detail)."""
    res: dict = {"steps": [], "ok": True}
    t_all = time.monotonic()
    work = keep_venv or Path(tempfile.mkdtemp(prefix="np-release-check-"))
    venv = work / "venv"
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", SPATIAL_STANDARDS_NO_CACHE="1",
               PIP_DISABLE_PIP_VERSION_CHECK="1")

    def step(name: str, ok: bool, detail: str = "", secs: float | None = None):
        res["steps"].append({"name": name, "ok": ok, "detail": detail.strip(),
                             "secs": secs})
        if not ok:
            res["ok"] = False
        return ok

    try:
        # 1. fresh venv + install from PyPI (exactly what `pipx install` resolves today)
        t = time.monotonic()
        if venv.exists():
            shutil.rmtree(venv)
        r = sh([python, "-m", "venv", str(venv)])
        if not step("create venv", r.returncode == 0, r.stderr, time.monotonic() - t):
            return res
        pip = [str(venv / "bin" / "python"), "-m", "pip"]
        t = time.monotonic()
        r = sh(pip + ["install", "-q", "--upgrade", "pip"], timeout=300, env=env)
        r = sh(pip + ["install", "-q", f"{PACKAGE}[full]"], timeout=2400, env=env)
        installed = sh(pip + ["show", PACKAGE], env=env).stdout
        ver = re.search(r"^Version:\s*(\S+)", installed, re.M)
        res["installed_version"] = ver.group(1) if ver else None
        if not step(f"pip install '{PACKAGE}[full]' from PyPI", r.returncode == 0,
                    r.stderr[-1500:], time.monotonic() - t):
            return res
        frozen = sh(pip + ["freeze"], env=env).stdout
        res["resolved"] = {
            name: m.group(1) for name in DEPS
            if (m := re.search(rf"^{re.escape(name)}==(\S+)", frozen, re.M | re.I))}

        bin_dir = venv / "bin"
        cli = str(bin_dir / "spatial-standards")

        # 2. the CLI starts and every tool resolves from the venv
        t = time.monotonic()
        r = sh([cli, "--version"], env=env)
        step("spatial-standards --version", r.returncode == 0, r.stdout + r.stderr,
             time.monotonic() - t)
        for tool in ("demucs", "audio-separator", "yt-dlp", "deno"):
            step(f"{tool} present in venv", (bin_dir / tool).exists(),
                 "" if (bin_dir / tool).exists() else f"missing {bin_dir / tool}")

        # 3. yt-dlp still talks to YouTube (metadata only, no download)
        t = time.monotonic()
        r = sh([str(bin_dir / "yt-dlp"), "--no-cookies-from-browser", "--simulate",
                "--no-playlist", "--print", "%(title)s", YT_PROBE_URL], timeout=180,
               env=dict(env, PATH=f"{bin_dir}:{env.get('PATH', '')}"))
        step("yt-dlp resolves a YouTube video", r.returncode == 0 and bool(r.stdout.strip()),
             (r.stdout.strip()[:80] + "\n" + r.stderr.strip()[-600:]), time.monotonic() - t)

        # 4. the pipeline end to end on CPU with the built-in config (no API)
        clip, clip_desc = make_clip(clip_src, work / "clip.wav")
        res["clip"] = clip_desc
        out = work / "out"
        t = time.monotonic()
        r = sh([cli, str(clip), "--default-config", "--out", str(out),
                "--artist", "Release Check", "--title", "Smoke"], timeout=3600, cwd=repo,
               env=env)
        flacs = list(out.rglob("*.flac"))
        detail = (r.stdout + r.stderr)[-1500:]
        ok = r.returncode == 0 and bool(flacs)
        if ok:
            probe = sh(["ffprobe", "-v", "error", "-show_entries",
                        "stream=channels,channel_layout,bits_per_raw_sample",
                        "-of", "csv=p=0", str(flacs[0])])
            ok = "8,7.1,24" in probe.stdout.replace(" ", "")
            detail = f"{flacs[0].name}: {probe.stdout.strip()}"
        step(f"pipeline on CPU, default config ({clip_desc})", ok, detail, time.monotonic() - t)

        # 5. the model path — one real Natural Perspective design, no web research
        key_present = bool(os.environ.get("ANTHROPIC_API_KEY")) or \
            any((p / ".env").exists() and "ANTHROPIC_API_KEY=" in (p / ".env").read_text()
                for p in (repo,))
        if key_present:
            t = time.monotonic()
            out2 = work / "out_model"
            r = sh([cli, str(clip), "--no-research", "--out", str(out2),
                    "--artist", "Release Check", "--title", "Model"], timeout=3600, cwd=repo,
                   env=env)
            side = list(out2.rglob("*.config.json"))
            used = None
            if side:
                try:
                    used = json.loads(side[0].read_text()).get("model")
                except Exception:
                    used = None
            ok = r.returncode == 0 and used is not None and not str(used).startswith("default")
            fallback = re.search(r"model unavailable \((.*?)\); using default", r.stdout + r.stderr)
            detail = f"model used: {used}" + (f"\nfallback reason: {fallback.group(1)[:400]}"
                                              if fallback else "")
            step("Natural Perspective model call (claude, no research)", ok, detail,
                 time.monotonic() - t)
        else:
            step("Natural Perspective model call", True,
                 "skipped — no ANTHROPIC_API_KEY (env or repo .env)")
    except Exception as e:  # the check itself broke; report, don't blame the product
        step("release check internals", False, "".join(traceback.format_exception(e))[-1500:])
        res["internal_error"] = True
    finally:
        res["secs"] = time.monotonic() - t_all
        if keep_venv is None:
            shutil.rmtree(work, ignore_errors=True)
    return res


# ----------------------------------------------------------------------------- report

def verdict(st: dict, smoke: dict | None) -> tuple[str, str]:
    """(headline, reason)."""
    if smoke is not None and not smoke["ok"]:
        failed = [s["name"] for s in smoke["steps"] if not s["ok"]]
        if smoke.get("internal_error"):
            return ("CHECK BROKEN — release check itself failed",
                    "the monthly check hit an internal error; see the smoke-test section")
        return ("RELEASE NEEDED — fresh install is broken",
                "failed: " + "; ".join(failed))
    pending = bool(st["commits"]) or bool(st["unreleased_bullets"])
    if pending:
        n = len(st["commits"])
        return ("RELEASE READY — unreleased work on main",
                f"{n} commit(s) since {st['last_tag']} and "
                f"{len(st['unreleased_bullets'])} changelog entr(y/ies) waiting; "
                "the shipped release still installs and runs")
    return ("NO RELEASE NEEDED", "nothing unreleased; the shipped release installs and runs")


def render(st: dict, deps: list[dict], smoke: dict | None) -> str:
    head, why = verdict(st, smoke)
    today = date.today().isoformat()
    L: list[str] = []
    L.append(f"# Natural Perspective — {head}")
    L.append("")
    L.append(f"Monthly release check, {today}. {why}.")
    L.append("")
    L.append("## Versions")
    L.append("")
    L.append("| What | Value |")
    L.append("|---|---|")
    L.append(f"| On PyPI | {st['pypi_version'] or '?'} (uploaded {days_ago(st['pypi_uploaded'])}) |")
    L.append(f"| Last tag | {st['last_tag'] or '—'} ({st['last_tag_date'] or '?'}) |")
    L.append(f"| Version on {st['ref']} | {st['main_version'] or '?'} |")
    L.append(f"| Commits since tag | {len(st['commits'])} |")
    L.append(f"| Unreleased changelog entries | {len(st['unreleased_bullets'])} |")
    if st.get("open_issues") is not None:
        L.append(f"| Open GitHub issues | {st['open_issues']} (stars {st.get('stars')}) |")
    if not st["fetch_ok"]:
        L.append("")
        L.append(f"**git fetch failed** — numbers above are from the local checkout: "
                 f"{st['fetch_err'][:200]}")
    if st["pypi_version"] and st["main_version"] and st["commits"] and \
            version_key(st["main_version"].split(".dev")[0]) <= version_key(st["pypi_version"]):
        L.append("")
        L.append(f"**Version not bumped:** main is still {st['main_version']} with "
                 f"{len(st['commits'])} commit(s) past {st['pypi_version']}. Bump pyproject "
                 "and __init__ before tagging (RELEASING.md).")
    if st["commits"]:
        L.append("")
        L.append(f"### Commits since {st['last_tag']}")
        L.append("")
        for c in st["commits"][:20]:
            L.append(f"- {c}")
        if len(st["commits"]) > 20:
            L.append(f"- … and {len(st['commits']) - 20} more")
    if st["unreleased_bullets"]:
        L.append("")
        L.append(f"### {st['unreleased_heading']}")
        L.append("")
        for b in st["unreleased_bullets"][:15]:
            first = b[2:].split("\n")[0]
            L.append(f"- {first[:160]}")

    L.append("")
    L.append("## Fresh install from PyPI")
    L.append("")
    if smoke is None:
        L.append("Skipped (--skip-smoke).")
    else:
        L.append(f"Installed `{PACKAGE}[full]` = {smoke.get('installed_version') or '?'} into a "
                 f"fresh venv, CPU only, and ran it. Total {fmt_secs(smoke.get('secs', 0))}.")
        L.append("")
        L.append("| Step | Result | Time |")
        L.append("|---|---|---|")
        for s in smoke["steps"]:
            t = fmt_secs(s["secs"]) if s.get("secs") is not None else ""
            L.append(f"| {s['name']} | {'OK' if s['ok'] else 'FAILED'} | {t} |")
        failed = [s for s in smoke["steps"] if not s["ok"]]
        for s in failed:
            L.append("")
            L.append(f"**{s['name']}** failed:")
            L.append("")
            for ln in (s["detail"] or "(no output)").splitlines()[-25:]:
                L.append(f"- {ln[:200]}" if ln.strip() else "")
        okd = [s for s in smoke["steps"] if s["ok"] and s["detail"] and
               ("skipped" in s["detail"] or s["name"].startswith("Natural") or
                s["name"].startswith("pipeline"))]
        if okd:
            L.append("")
            for s in okd:
                L.append(f"- {s['name']}: {s['detail'].splitlines()[0][:160]}")
        if smoke.get("resolved"):
            L.append("")
            L.append("### What a fresh install resolves today")
            L.append("")
            L.append("| Package | Installed | Latest on PyPI | Latest released |")
            L.append("|---|---|---|---|")
            latest = {d["name"].lower(): d for d in deps}
            for name in DEPS:
                d = latest.get(name.lower(), {})
                L.append(f"| {name} | {smoke['resolved'].get(name, '—')} | "
                         f"{d.get('latest', '?')} | {days_ago(d.get('released'))} |")
    if smoke is None:
        L.append("")
        L.append("## Dependency releases")
        L.append("")
        L.append("| Package | Latest on PyPI | Released |")
        L.append("|---|---|---|")
        for d in deps:
            L.append(f"| {d['name']} | {d['latest']} | {days_ago(d['released'])} |")

    L.append("")
    L.append("---")
    L.append("")
    L.append("To release: bump the version, finish the CHANGELOG section, commit, "
             "`git tag -a vX.Y.Z && git push origin vX.Y.Z` (RELEASING.md).")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--out", type=Path, help="write the Markdown report here (default stdout)")
    ap.add_argument("--clip", type=Path, help="audio file to trim a 30 s test clip from "
                                              "(default: <repo>/the_sign.mp3 if present)")
    ap.add_argument("--skip-smoke", action="store_true", help="repo/PyPI state only")
    ap.add_argument("--keep-venv", type=Path, metavar="DIR",
                    help="build the smoke venv under DIR and keep it (debugging)")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter to create the smoke venv with (default: this one)")
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    clip = args.clip or (repo / "the_sign.mp3")
    st = repo_state(repo)
    deps = dependency_state()
    smoke = None if args.skip_smoke else smoke_test(repo, clip, args.python, args.keep_venv)
    report = render(st, deps, smoke)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
