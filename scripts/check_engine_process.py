#!/usr/bin/env python3
"""
Checks for the audio engine running in its own process (Phase 2).

    .venv/bin/python scripts/check_engine_process.py

  crash     the engine process is killed while playing: the interface stays
            up, says so, restarts the engine, and Play works again
  bounce    a long Bounce to Disk: the interface stays responsive (heartbeat),
            the progress bar moves, Cancel stops it and removes the file, and
            a following bounce works
  batch     a folder batch cancelled during a render: stops at once, the
            unfinished file is removed
  quit      closing the engine ends its process

Prints a JSON summary on the line starting with RESULT and exits with 1 if a
check failed. The app runs with a throwaway HOME.
"""
import json, os, signal, sys, tempfile, time, wave, traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix="cecilia-proc-")
os.environ["HOME"] = os.path.join(WORK, "home")
os.makedirs(os.environ["HOME"])
import numpy as np


def write_wav(path, seconds, sr=48000):
    t = np.arange(int(sr * seconds)) / sr
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    pcm = (np.stack([data, data], axis=1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())


SND = os.path.join(WORK, "test.wav")
write_wav(SND, 4)
BATCH = os.path.join(WORK, "batch")
os.makedirs(BATCH)
for i in range(3):
    write_wav(os.path.join(BATCH, "snd%d.wav" % i), 4)

sys.path.insert(0, REPO)
import wx
import Resources.splash as splash

results = {}
failures = []
dialogs = []


def log(*a):
    print(*a, flush=True)


def check(name, ok, detail=""):
    results[name] = bool(ok)
    if not ok:
        failures.append("%s %s" % (name, detail))
    log("%-32s %s %s" % (name, "ok" if ok else "FAILED", detail))


def C():
    import Resources.CeciliaLib as CL
    return CL


class Heartbeat:
    """Longest gap between 5 ms timer ticks on the main thread."""
    def __init__(self):
        self.timer = wx.Timer()
        self.timer.Bind(wx.EVT_TIMER, self.tick)
        self.last = None
        self.worst = 0.0

    def start(self):
        self.last = time.monotonic(); self.worst = 0.0
        self.timer.Start(5)

    def tick(self, evt):
        now = time.monotonic()
        self.worst = max(self.worst, now - self.last)
        self.last = now

    def stop(self):
        self.timer.Stop()
        return self.worst * 1000


def open_module(rel, total):
    CL = C()
    CL.getVar("mainFrame").onOpen(os.path.join(REPO, "Resources", "modules", rel), True)
    yield 500
    cp = CL.getControlPanel()
    for cf in cp.getCfileinList():
        cf.onLoadFile(SND)
    cp.setTotalTime(total, True); cp.updateDurationSlider()
    CL.setVar("nchnls", 2); CL.setVar("audioFileType", "wav"); CL.setVar("sampSize", 0)
    cp.gainSlider.SetValue(-60)


def scenarios():
    CL = C()
    engine = CL.getEngine()
    from Resources import engine_client
    check("remote_engine", isinstance(engine, engine_client.RemoteEngine), type(engine).__name__)
    if not isinstance(engine, engine_client.RemoteEngine):
        return

    # ---- crash while playing
    yield from open_module("Time/Granulator.c5", 10.0)
    cp = CL.getControlPanel()
    cp.transportButtons.setPlay(True); cp.onPlayStop(True)
    yield 800
    check("crash_playing_before", engine.isRunning())
    pid = engine._proc.pid
    os.kill(pid, signal.SIGKILL)
    yield 1500
    check("crash_dialog", any("Audio engine stopped" in d for d in dialogs), str(dialogs))
    check("crash_restarted", engine._alive() and engine._proc.pid != pid)
    check("crash_not_running", not engine.isRunning())
    check("crash_play_reset", not cp.transportButtons.getPlay())
    del dialogs[:]
    cp.transportButtons.setPlay(True); cp.onPlayStop(True)
    yield 800
    check("crash_play_again", engine.isRunning())
    cp.onPlayStop(False)
    yield 400
    check("crash_stop_again", not engine.isRunning())

    # ---- long bounce: responsive, progress, cancel
    yield from open_module("Spectral/SpectralGate.c5", 600.0)
    cp = CL.getControlPanel()
    out = os.path.join(WORK, "long.wav")
    cp.outputFilename = out
    CL.autoRename = lambda f, *a, **k: f
    hb = Heartbeat(); hb.start()
    cp.onBounceToDisk()
    yield 1500
    stall = hb.stop()
    check("bounce_running", engine.isRunning())
    check("bounce_responsive", stall < 100, "max stall %.0f ms" % stall)
    gauge = cp.bounce_gauge.GetValue() if cp.bounce_gauge else -1
    check("bounce_progress", gauge > 0, "gauge %d/1000" % gauge)
    t0 = time.monotonic()
    cp.onCancelBounce()
    took = time.monotonic() - t0
    yield 300
    check("bounce_cancel_fast", took < 3, "%.2f s" % took)
    check("bounce_cancel_stopped", not engine.isRunning())
    check("bounce_cancel_removed", not os.path.exists(out))
    check("bounce_cancel_dialog_closed", cp.bounce_dlg is None)
    # a short bounce still works after the cancel (the engine was restarted)
    cp.setTotalTime(2.0, True); cp.updateDurationSlider()
    out2 = os.path.join(WORK, "short.wav")
    cp.outputFilename = out2
    cp.onBounceToDisk()
    t0 = time.monotonic()
    yield 200
    while engine.isRunning() and time.monotonic() - t0 < 30:
        yield 50
    yield 300
    ok = os.path.isfile(out2) and os.path.getsize(out2) > 100000
    check("bounce_after_cancel", ok, str(os.path.getsize(out2)) if os.path.isfile(out2) else "missing")
    check("bounce_dialog_closed_at_end", cp.bounce_dlg is None)
    check("no_error_dialogs", not dialogs, str(dialogs))

    # ---- batch cancelled during a render
    cp.setTotalTime(600.0, True); cp.updateDurationSlider()
    cfilein = cp.getCfileinList()[0]
    cfilein.onLoadFile(os.path.join(BATCH, "snd0.wav"))
    CL.setVar("useSoundDur", 0)
    updates = {"n": 0}
    OrigDlg = wx.ProgressDialog

    class FakeProgress:
        def __init__(self, *a, **k):
            pass
        def SetMinSize(self, *a): pass
        def SetClientSize(self, *a): pass
        def Destroy(self): pass
        def Update(self, value, msg=""):
            updates["n"] += 1
            updates["last"] = value
            # cancel once the first file has been rendering for a while
            return (updates["n"] < 20, False)

    wx.ProgressDialog = FakeProgress
    mf = CL.getVar("mainFrame")
    t0 = time.monotonic()
    mf.applyBatchProcessingFolder("out")
    while mf.batchRunner is not None and time.monotonic() - t0 < 30:
        yield 50
    took = time.monotonic() - t0
    wx.ProgressDialog = OrigDlg
    yield 300
    outdir = os.path.join(BATCH, "out")
    left = os.listdir(outdir) if os.path.isdir(outdir) else []
    check("batch_cancel_fast", took < 5, "%.2f s" % took)
    check("batch_cancel_stopped", not engine.isRunning())
    check("batch_cancel_removed", not left, str(left))
    check("batch_progress_within_file", 0 < updates.get("last", 0) < 100, str(updates))

    # ---- quit
    proc = engine._proc
    engine.close()
    check("quit_engine_exited", proc.poll() is not None)


def finish():
    results["failures"] = failures
    print("RESULT " + json.dumps(results, sort_keys=True), flush=True)
    os._exit(1 if failures else 0)


def run():
    CL = C()
    CL.showErrorDialog = lambda title, msg: dialogs.append("%s: %s" % (title, msg[:200]))
    gen = scenarios()

    def step():
        try:
            d = next(gen)
        except StopIteration:
            finish(); return
        except Exception:
            failures.append(traceback.format_exc())
            log(traceback.format_exc())
            finish(); return
        wx.CallLater(d, step)
    step()


Orig = splash.CeciliaSplashScreen


class Patched(Orig):
    def __init__(self, parent, img, callback):
        def cb():
            callback(); wx.CallLater(1500, run)
        Orig.__init__(self, parent, img=img, callback=cb)


splash.CeciliaSplashScreen = Patched
os.chdir(REPO)
sys.argv = [os.path.join(REPO, "Cecilia5.py")]
import runpy
runpy.run_path(sys.argv[0], run_name="__main__")
