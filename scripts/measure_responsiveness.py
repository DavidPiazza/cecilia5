#!/usr/bin/env python3
"""
Measure how responsive the interface stays in typical situations.

Runs Cecilia5 from source with CECILIA_PROFILE=1 and a throwaway HOME, then
plays scripted scenarios and reports, for each, how long the main (GUI) thread
was blocked: the worst stall, the 95th percentile, and the total time spent in
stalls longer than 50 ms ("frozen"). Also reports Play-click -> audio started.

    .venv/bin/python scripts/measure_responsiveness.py --out .golden/resp-base.json
    .venv/bin/python scripts/measure_responsiveness.py --compare .golden/resp-base.json

Scenarios:
    idle        3 s doing nothing
    play        Granulator playing 5 s with every slider reading automation
    bounce      20 s offline render of a spectral module (Bounce to Disk path)
    batch       folder batch, 4 files x 5 s, same module

Note: "play" sends (almost silent, -60 dB) audio to the default output device.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import wave

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

parser = argparse.ArgumentParser()
parser.add_argument("--out", default=None)
parser.add_argument("--compare", default=None)
parser.add_argument("--only", default=None, help="comma separated scenario names")
args = parser.parse_args()

WORK = tempfile.mkdtemp(prefix="cecilia-resp-")
os.environ["HOME"] = os.path.join(WORK, "home")
os.environ["CECILIA_PROFILE"] = "1"
os.makedirs(os.environ["HOME"])
SOUNDS = os.path.join(WORK, "sounds")
os.makedirs(SOUNDS)

import numpy as np  # noqa: E402


def write_test_sound(path, seconds=4.0, sr=48000, seed=0):
    t = np.arange(int(sr * seconds)) / sr
    rng = np.random.default_rng(seed)
    tone = 0.3 * np.sin(2 * np.pi * (110 + 40 * seed) * t) + 0.05 * rng.standard_normal(t.size)
    pcm = (np.clip(np.stack([tone, tone], 1), -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


SOUND_FILES = []
for i in range(4):
    p = os.path.join(SOUNDS, "snd%d.wav" % i)
    write_test_sound(p, seed=i)
    SOUND_FILES.append(p)

sys.path.insert(0, REPO)
os.chdir(REPO)
import wx  # noqa: E402
import Resources.splash as splash  # noqa: E402

results = {}


def log(*a):
    print(*a, flush=True)


def finish():
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(results, f, indent=1, sort_keys=True)
        log("Wrote", args.out)
    if args.compare:
        with open(args.compare) as f:
            base = json.load(f)
        log("\n%-8s %22s %22s %22s %18s" % ("scenario", "max stall ms", "p95 stall ms", "frozen ms", "play latency ms"))
        for name in results:
            a, b = base.get(name, {}), results[name]

            def pair(k):
                return "%9s -> %-9s" % (a.get(k, "-"), b.get(k, "-"))
            lat_a = dict(a.get("intervals", [])).get("play_latency", "-")
            lat_b = dict(b.get("intervals", [])).get("play_latency", "-")
            log("%-8s %22s %22s %22s %8s -> %-8s" % (name, pair("max_ms"), pair("p95_ms"), pair("frozen_ms"), lat_a, lat_b))
    shutil.rmtree(WORK, ignore_errors=True)
    sys.stdout.flush()
    os._exit(0)


def run():
    import Resources.CeciliaLib as C
    from Resources import profiling
    from Resources.constants import MODULES_PATH

    C.showErrorDialog = lambda title, msg: log("DIALOG:", title, msg)
    mf = C.getVar("mainFrame")

    def open_module(rel, then):
        mf.onOpen(os.path.join(MODULES_PATH, rel), True)

        def loaded():
            cp = C.getControlPanel()
            for cf in cp.getCfileinList():
                cf.onLoadFile(SOUND_FILES[0])
            C.setVar("audioFileType", "wav")
            C.setVar("sampSize", 0)
            then()
        wx.CallLater(500, loaded)

    def measure(name, start, is_done, cleanup=None, timeout=120):
        def begin():
            profiling.reset()
            t0 = time.time()
            start()

            def poll():
                if not is_done() and time.time() - t0 < timeout:
                    wx.CallLater(20, poll)
                    return
                results[name] = profiling.stats()
                results[name]["wall_s"] = round(time.time() - t0, 2)
                results[name]["totalTime"] = round(C.getVar("totalTime"), 2)
                log("%-8s %s" % (name, results[name]))
                if cleanup:
                    cleanup()
                wx.CallLater(800, next_scenario)
            wx.CallLater(20, poll)
        begin()

    def running():
        return C.getVar("audioServer").isAudioServerRunning()

    # --- scenarios -------------------------------------------------------
    def sc_idle():
        t = {}
        measure("idle", lambda: t.setdefault("end", time.time() + 3), lambda: time.time() > t["end"])

    def sc_play():
        def go():
            cp = C.getControlPanel()
            cp.setTotalTime(30, True); cp.updateDurationSlider()  # else the slider's next repaint restores the old duration
            for s in C.getVar("userSliders"):
                if hasattr(s, "setPlay"):
                    s.setPlay(1)
            C.getVar("audioServer").setAmp(-60)
            t = {}

            def start():
                t["end"] = time.time() + 5
                mf.onPlayStop(True)
            measure("play", start, lambda: time.time() > t["end"],
                    cleanup=lambda: mf.onPlayStop(False))
        open_module("Time/Granulator.c5", go)

    def sc_bounce():
        def go():
            C.getControlPanel().setTotalTime(20, True); C.getControlPanel().updateDurationSlider()  # else the slider's next repaint restores the old duration
            out = os.path.join(WORK, "bounce.wav")
            t = {}

            def start():
                t["started"] = True
                C.getControlPanel().onBatchProcessing(out)
            measure("bounce", start, lambda: not running())
        open_module("Spectral/Vectral.c5", go)

    def sc_batch():
        def go():
            C.getControlPanel().setTotalTime(5, True); C.getControlPanel().updateDurationSlider()  # else the slider's next repaint restores the old duration
            t = {}

            def start():
                # the batch loop blocks until done; measure() polls afterwards
                wx.CallAfter(lambda: (mf.applyBatchProcessingFolder("batch"), t.setdefault("done", True)))
            measure("batch", start, lambda: t.get("done", False))
        open_module("Spectral/Vectral.c5", go)

    order = [("idle", sc_idle), ("play", sc_play), ("bounce", sc_bounce), ("batch", sc_batch)]
    if args.only:
        wanted = args.only.split(",")
        order = [o for o in order if o[0] in wanted]
    queue = list(order)

    def next_scenario():
        if not queue:
            finish()
            return
        name, fn = queue.pop(0)
        log("--- %s" % name)
        fn()

    globals()["next_scenario"] = next_scenario
    next_scenario()


def next_scenario():  # replaced in run()
    pass


OrigSplash = splash.CeciliaSplashScreen


class PatchedSplash(OrigSplash):
    def __init__(self, parent, img, callback):
        def cb():
            callback()
            wx.CallLater(2000, run)
        OrigSplash.__init__(self, parent, img=img, callback=cb)


splash.CeciliaSplashScreen = PatchedSplash
sys.argv = [os.path.join(REPO, "Cecilia5.py")]
import runpy  # noqa: E402
runpy.run_path(sys.argv[0], run_name="__main__")
