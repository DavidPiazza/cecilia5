#!/usr/bin/env python3
"""
Golden-render regression harness.

Launches Cecilia5 (from source), opens every built-in module, feeds it
deterministic test sounds, bounces 2 seconds offline to 16-bit WAV and records
RMS / peak / length per module in a JSON file. Run it before and after a
refactor and compare the two files to catch behaviour changes.

    .venv/bin/python scripts/render_all_modules.py --out .golden/base.json
    .venv/bin/python scripts/render_all_modules.py --out .golden/new.json --compare .golden/base.json

Options:
    --only SUBSTR   only render modules whose path contains SUBSTR
    --dur SECONDS   render length (default 2)
    --keep          keep the rendered WAV files (in a temp dir, path printed)

The app runs with a throwaway HOME so your preferences are not touched.
"""
import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import time
import wave

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

parser = argparse.ArgumentParser()
parser.add_argument("--out", default=os.path.join(REPO, ".golden", "renders.json"))
parser.add_argument("--compare", default=None)
parser.add_argument("--only", default=None)
parser.add_argument("--dur", type=float, default=2.0)
parser.add_argument("--keep", action="store_true")
parser.add_argument("--tolerance", type=float, default=0.05,
                    help="relative RMS difference reported as a change (default 5%%)")
args = parser.parse_args()

WORK = tempfile.mkdtemp(prefix="cecilia-golden-")
os.environ["HOME"] = os.path.join(WORK, "home")   # before Cecilia reads prefs
os.makedirs(os.environ["HOME"])
SOUNDS = os.path.join(WORK, "sounds")
OUTDIR = os.path.join(WORK, "out")
os.makedirs(SOUNDS)
os.makedirs(OUTDIR)

import numpy as np  # noqa: E402


def write_wav(path, data, sr=48000):
    data = np.clip(data, -1, 1)
    pcm = (data * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(data.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav(path):
    with wave.open(path, "rb") as w:
        n, ch, width = w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError("expected 16-bit output, got %d bytes/sample" % width)
    return np.frombuffer(raw, "<i2").reshape(-1, ch).astype(np.float64) / 32768.0, ch


def make_test_sounds():
    """Deterministic stereo material: a sweep with harmonics and some noise bursts."""
    sr, dur = 48000, 4.0
    t = np.arange(int(sr * dur)) / sr
    rng = np.random.default_rng(1234)
    freq = 110 * (2 ** (t / dur * 4))
    phase = 2 * np.pi * np.cumsum(freq) / sr
    tone = 0.3 * np.sin(phase) + 0.1 * np.sin(2 * phase) + 0.05 * np.sin(3 * phase)
    env = (np.sin(2 * np.pi * 2 * t) > 0.6).astype(float)
    noise = 0.2 * rng.standard_normal(t.size) * env
    left, right = tone + noise, tone * 0.8 - noise * 0.5
    path = os.path.join(SOUNDS, "test.wav")
    write_wav(path, np.stack([left, right], axis=1), sr)
    return path


TEST_SOUND = make_test_sounds()

sys.path.insert(0, REPO)
os.chdir(REPO)
import wx  # noqa: E402
import Resources.splash as splash  # noqa: E402

results = {}
errors = []


def log(*a):
    print(*a, flush=True)


def finish():
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=1, sort_keys=True)
    log("\nWrote %d results to %s" % (len(results), args.out))
    status = 0
    if args.compare:
        status = compare(args.compare, results)
    if args.keep:
        log("Rendered files kept in", OUTDIR)
    else:
        shutil.rmtree(WORK, ignore_errors=True)
    sys.stdout.flush()
    os._exit(status)


# Modules whose output varies between runs even with a fixed seed (random
# choices pyo's global seed doesn't reach). Compared with a looser tolerance.
NONDETERMINISTIC = {
    "Multiband/MultiBandBeatMaker.c5", "Pitch/RandomAccumulator.c5",
    "Synthesis/StochGrains.c5", "Synthesis/StochGrains2.c5", "Time/BeatMaker.c5",
}
NONDETERMINISTIC_TOLERANCE = 0.35


def compare(basefile, new):
    with open(basefile) as f:
        base = json.load(f)
    changed = []
    for key in sorted(set(base) | set(new)):
        a, b = base.get(key), new.get(key)
        if a is None or b is None:
            changed.append("%s: %s" % (key, "new" if a is None else "missing"))
            continue
        if a.get("error") or b.get("error"):
            if a.get("error") != b.get("error"):
                changed.append("%s: error %r -> %r" % (key, a.get("error"), b.get("error")))
            continue
        if a["frames"] != b["frames"] or a["channels"] != b["channels"]:
            changed.append("%s: shape %sx%s -> %sx%s" % (key, a["frames"], a["channels"], b["frames"], b["channels"]))
            continue
        ra, rb = a["rms"], b["rms"]
        denom = max(ra, rb, 1e-6)
        tol = NONDETERMINISTIC_TOLERANCE if key in NONDETERMINISTIC else args.tolerance
        if abs(ra - rb) / denom > tol:
            changed.append("%s: rms %.4f -> %.4f" % (key, ra, rb))
    log("\n=== Compared with %s: %d of %d modules differ ===" % (basefile, len(changed), len(set(base) | set(new))))
    for c in changed:
        log("  " + c)
    return 1 if changed else 0


def run():
    import Resources.CeciliaLib as C
    from Resources.constants import MODULES_PATH, FILE_EXTENSION

    dialogs = []
    C.showErrorDialog = lambda title, msg: dialogs.append("%s: %s" % (title, msg))

    server = C.getVar("audioServer")
    orig_reinit = server.reinit

    def seeded_reinit():
        orig_reinit()
        server.server.setGlobalSeed(1234)
    server.reinit = seeded_reinit

    C.setVar("audioFileType", "wav")
    C.setVar("sampSize", 0)  # 16 bit int
    C.setVar("nchnls", 2)

    modules = []
    for root, dirs, files in os.walk(MODULES_PATH):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(FILE_EXTENSION):
                path = os.path.join(root, f)
                if args.only is None or args.only in path:
                    modules.append(path)
    log("Rendering %d modules, %.1f s each" % (len(modules), args.dur))
    state = {"i": -1, "t0": 0, "out": None, "key": None}

    def next_module():
        state["i"] += 1
        if state["i"] >= len(modules):
            finish()
            return
        path = modules[state["i"]]
        key = os.path.relpath(path, MODULES_PATH)
        state["key"] = key
        del dialogs[:]
        try:
            import random
            random.seed(1234)
            C.getVar("mainFrame").onOpen(path, True)
        except Exception as e:
            results[key] = {"error": "%s: %s" % (type(e).__name__, e)}
            log("%-45s ERROR %s" % (key, results[key]["error"]))
            wx.CallAfter(next_module)
            return
        # The interface builds its grapher with wx.CallLater(100, ...): wait for it.
        wx.CallLater(400, start_render)

    def start_render():
        key = state["key"]
        try:
            cp = C.getControlPanel()
            for cf in cp.getCfileinList():
                cf.onLoadFile(TEST_SOUND)
            cp.setTotalTime(args.dur, True); cp.updateDurationSlider()  # else the slider's next repaint restores the old duration
            C.setVar("nchnls", 2)
            C.setVar("audioFileType", "wav")
            C.setVar("sampSize", 0)
            out = os.path.join(OUTDIR, key.replace(os.sep, "__").replace(".c5", ".wav"))
            state["out"] = out
            state["t0"] = time.time()
            cp.onBatchProcessing(out)
        except Exception as e:
            results[key] = {"error": "%s: %s" % (type(e).__name__, e)}
            log("%-45s ERROR %s" % (key, results[key]["error"]))
            wx.CallAfter(next_module)
            return
        wx.CallLater(50, wait_render)

    def wait_render():
        key = state["key"]
        if C.getVar("audioServer").isAudioServerRunning() and time.time() - state["t0"] < 60:
            wx.CallLater(20, wait_render)
            return
        elapsed = time.time() - state["t0"]
        out = state["out"]
        try:
            if dialogs:
                raise RuntimeError(" | ".join(dialogs))
            data, ch = read_wav(out)
            rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
            peak = float(np.max(np.abs(data))) if data.size else 0.0
            results[key] = {"frames": int(data.shape[0]), "channels": ch,
                            "rms": round(rms, 6), "peak": round(peak, 6),
                            "render_s": round(elapsed, 3)}
            log("%-45s rms %.4f peak %.4f  %d frames  %.2fs" % (key, rms, peak, data.shape[0], elapsed))
        except Exception as e:
            results[key] = {"error": "%s: %s" % (type(e).__name__, e)}
            log("%-45s ERROR %s" % (key, results[key]["error"]))
        # let queued stop/close-dialog callbacks run before the next module
        wx.CallLater(100, next_module)

    next_module()


OrigSplash = splash.CeciliaSplashScreen


class PatchedSplash(OrigSplash):
    def __init__(self, parent, img, callback):
        def cb():
            callback()
            wx.CallLater(1500, run)
        OrigSplash.__init__(self, parent, img=img, callback=cb)


splash.CeciliaSplashScreen = PatchedSplash
sys.argv = [os.path.join(REPO, "Cecilia5.py")]
import runpy  # noqa: E402
runpy.run_path(sys.argv[0], run_name="__main__")
