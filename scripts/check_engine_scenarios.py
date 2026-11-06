#!/usr/bin/env python3
"""
Scenario checks for the boundary between the interface and the audio engine.

Runs Cecilia5 from the checkout in the current directory, so the same file can
drive master and a branch (run it from a worktree of each) and the outputs
compared. Prints a JSON summary on the line starting with RESULT.

    .venv/bin/python scripts/check_engine_scenarios.py

Offline scenarios bounce 2 s with non-default widget state: automation lines,
range sliders, sampler settings, popups/toggles/gens, plugins with knob
automation, OSC and MIDI bindings. The live scenario plays in realtime and
changes things while playing (slider, popup, plugin swap and move), records
automation, opens the spectrum window and lets play reach its end. Also
covers play/stop, MIDI learn (interface side), modules that fail to compile
or to run, and recording to a file.

The app runs with a throwaway HOME so your preferences are not touched.
"""
import json, os, sys, tempfile, time, wave, traceback

REPO = os.getcwd()
WORK = tempfile.mkdtemp(prefix="cecilia-scen-")
os.environ["HOME"] = os.path.join(WORK, "home")
os.makedirs(os.environ["HOME"])
import numpy as np

def write_wav(path, data, sr=48000):
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(data.shape[1]); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())

def read_wav(path):
    with wave.open(path, "rb") as w:
        n, ch = w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    return np.frombuffer(raw, "<i2").reshape(-1, ch).astype(np.float64) / 32768.0

sr, dur = 48000, 4.0
t = np.arange(int(sr * dur)) / sr
rng = np.random.default_rng(1234)
phase = 2 * np.pi * np.cumsum(110 * (2 ** (t / dur * 4))) / sr
tone = 0.3 * np.sin(phase) + 0.1 * np.sin(2 * phase)
noise = 0.2 * rng.standard_normal(t.size) * (np.sin(2 * np.pi * 2 * t) > 0.6)
SND = os.path.join(WORK, "test.wav")
write_wav(SND, np.stack([tone + noise, tone * 0.8 - noise * 0.5], axis=1))

sys.path.insert(0, REPO)
import wx
import Resources.splash as splash

results = {}
problems = []

def log(*a):
    print(*a, flush=True)

def C():
    import Resources.CeciliaLib as CL
    return CL

def engine_running():
    CL = C()
    if hasattr(CL, "getEngine"):
        return CL.getEngine().isRunning()
    return CL.getVar("audioServer").isAudioServerRunning()

def audio_server():
    CL = C()
    if hasattr(CL, "getEngine"):
        return CL.getEngine().server
    return CL.getVar("audioServer")

MODULES = os.path.join(REPO, "Resources", "modules")

def ramp(line, a=0.2, b=0.8):
    lo, hi = line.getYrange()
    total = C().getVar("totalTime")
    if line.getLog():
        y0 = lo * (hi / lo) ** a; y1 = lo * (hi / lo) ** b
    else:
        y0 = lo + a * (hi - lo); y1 = lo + b * (hi - lo)
    line.setData([[0, y0], [total * 0.5, y1], [total, y0]])

def lines_named(prefix):
    return [l for l in C().getVar("grapher").plotter.getData() if l.getName().startswith(prefix)]

def open_module(rel):
    import random
    random.seed(1234)
    C().getVar("mainFrame").onOpen(os.path.join(MODULES, rel), True)

def prepare_inputs(total=2.0):
    cp = C().getControlPanel()
    for cf in cp.getCfileinList():
        cf.onLoadFile(SND)
    cp.setTotalTime(total, True); cp.updateDurationSlider()
    C().setVar("nchnls", 2); C().setVar("audioFileType", "wav"); C().setVar("sampSize", 0)

def bounce(name):
    out = os.path.join(WORK, name + ".wav")
    C().getControlPanel().onBatchProcessing(out)
    t0 = time.time()
    while engine_running() and time.time() - t0 < 30:
        yield 20
    yield 150
    d = read_wav(out)
    results[name] = {"rms": round(float(np.sqrt(np.mean(d ** 2))), 5),
                     "peak": round(float(np.max(np.abs(d))), 5), "frames": int(d.shape[0])}
    log(name, results[name])

def find(seq, name):
    for w in seq:
        if w.getName() == name:
            return w
    raise KeyError(name)

def scenarios():
    CL = C()
    # ---- 1. sampler + toggles + popups + slider automation (ChordMaker)
    open_module("Pitch/ChordMaker.c5"); yield 500
    prepare_inputs()
    for s in CL.getVar("userSliders"):
        if s.getName().startswith("transp"):
            for l in lines_named(s.getName()):
                ramp(l)
            s.setPlay(1)
    sampler = CL.getVar("userSamplers")[0]
    frame = sampler.getSamplerFrame()
    frame.gainSlider.setValue(-6); frame.transpSlider.setValue(3); frame.loopInSlider.setValue(0.5)
    frame.setLoopMode(2)
    for l in lines_named("sndxfade"):
        ramp(l)
    frame.loopXSlider.setPlay(1)
    find(CL.getVar("userTogglePopups"), "onoffv2").setValue(0)
    find(CL.getVar("userTogglePopups"), "winsize").setValue(4)
    for tp in CL.getVar("userTogglePopups"):
        if tp.getName().endswith("num"):   # polyphony voices
            tp.setValue(2)
    yield from bounce("chordmaker_auto")

    # ---- 2. range sliders with automation (LooperMod)
    open_module("Pitch/LooperMod.c5"); yield 500
    prepare_inputs()
    rng_ = find(CL.getVar("userSliders"), "amrange")
    for l in lines_named("amrange"):
        ramp(l, 0.1 if l.getLabel().endswith("min") else 0.6, 0.4 if l.getLabel().endswith("min") else 0.9)
    rng_.setPlay(1)
    find(CL.getVar("userSliders"), "fmrange").setValue([0.02, 0.1])
    find(CL.getVar("userTogglePopups"), "onofffm").setValue(0)
    find(CL.getVar("userTogglePopups"), "ampwave").setValue(3)
    yield from bounce("loopermod_range")

    # ---- 3. plugins with knob automation, gens, graphs (Granulator)
    open_module("Time/Granulator.c5"); yield 500
    prepare_inputs()
    cp = CL.getControlPanel()
    cp.replacePlugin(0, "Reverb"); cp.replacePlugin(1, "Filter"); cp.replacePlugin(2, "Disto")
    yield 200
    k = cp.plugins[0].getKnobs(); k[0].setValue(0.5); k[1].setValue(0.3)
    knob = cp.plugins[1].getKnobs()[0]
    for l in lines_named(knob.getName()):
        ramp(l)
    knob.setPlay(1)
    cp.plugins[2].getKnobs()[0].setValue(0.4)
    find(CL.getVar("userTogglePopups"), "discreet").setValue("1, 0.5, 2")
    find(CL.getVar("userSliders"), "cut").setValue(3000)
    find(CL.getVar("userTogglePopups"), "filttype").setValue(2)
    for l in lines_named("env"):
        ramp(l, 0.3, 1.0)
    pos = find(CL.getVar("userSliders"), "pos"); pos.setPlay(1)
    yield from bounce("granulator_plugins")

    # ---- 4. gen with many values + sampler (StateVar2)
    open_module("Filters/StateVar2.c5"); yield 500
    prepare_inputs()
    find(CL.getVar("userTogglePopups"), "order").setValue("0, 2, 4, 6")
    find(CL.getVar("userSliders"), "type").setValue(0.8)
    yield from bounce("statevar_gen")

    # ---- 5. live play
    open_module("Time/Granulator.c5"); yield 500
    prepare_inputs(total=3.0)
    cp = CL.getControlPanel()
    cp.gainSlider.SetValue(-60)
    counts = {"clock": 0, "meter": 0, "slider_auto": 0, "spectrum": 0}
    ocp_settime = cp.setTime
    def settime(*a):
        counts["clock"] += 1; return ocp_settime(*a)
    cp.setTime = settime
    orms = cp.vuMeter.setRms
    def setrms(*a, **k):
        counts["meter"] += 1; return orms(*a, **k)
    cp.vuMeter.setRms = setrms
    pos = find(CL.getVar("userSliders"), "pos")
    for l in lines_named("pos"):
        ramp(l)
    pos.setPlay(1)
    opos = pos.setValue
    def possv(v):
        counts["slider_auto"] += 1; return opos(v)
    pos.setValue = possv
    transp = find(CL.getVar("userSliders"), "transp")
    transp.setRec(1)
    before = [list(p) for p in lines_named("transp")[0].getData()]
    CL.setVar("showSpectrum", 1)
    cp.transportButtons.setPlay(True)
    cp.onPlayStop(True)
    yield 400
    if not engine_running():
        problems.append("live: not running after play")
    sf = CL.getVar("spectrumFrame")
    if sf is None:
        problems.append("live: no spectrum window")
    else:
        oupd = sf.spectrum.update
        def upd(points):
            counts["spectrum"] += 1; return oupd(points)
        sf.spectrum.update = upd
        sf.setAnalyzer(sf.spectrum.obj)   # re-register the patched update
        sf.specSetSize(type("E", (), {"GetInt": lambda self: 3})())
        sf.specZoom([0.0, 0.25])
    yield 300
    transp.entryReturn(1200)                 # slider move while playing (recorded)
    find(CL.getVar("userTogglePopups"), "filttype").setValue(2, True)   # popup -> module
    cp.replacePlugin(0, "Chorus")            # plugin swap while playing
    yield 300
    cp.plugins[0].getKnobs()[0].setValue(0.7)   # knob -> plugin
    transp.entryReturn(-1200)
    yield 300
    if hasattr(cp, "movePlugin"):
        try:
            cp.movePlugin(0, 1)
        except Exception as e:
            problems.append("movePlugin: %r" % e)
    t0 = time.time()
    while engine_running() and time.time() - t0 < 8:
        yield 50
    yield 600
    results["live"] = dict(counts)
    results["live"]["stopped_at_end"] = not engine_running()
    results["live"]["play_button_reset"] = not cp.transportButtons.getPlay() if hasattr(cp.transportButtons, "getPlay") else None
    after = [list(p) for p in lines_named("transp")[0].getData()]
    results["live"]["automation_recorded"] = after != before and len(after) > 3
    results["live"]["spectrum_closed"] = CL.getVar("spectrumFrame") is None
    log("live", results["live"])
    CL.setVar("showSpectrum", 0)

    # ---- 6. play/stop x3 quickly
    for i in range(3):
        cp = CL.getControlPanel()
        cp.transportButtons.setPlay(True); cp.onPlayStop(True)
        yield 500
        if not engine_running():
            problems.append("play/stop %d: not running" % i)
        cp.onPlayStop(False)
        yield 300
        if engine_running():
            problems.append("play/stop %d: still running" % i)
    results["playstop"] = "done"

    # ---- 7. OSC and MIDI bindings (sliders, range, sampler sliders)
    CL.setVar("useMidi", 1)
    open_module("Pitch/ChordMaker.c5"); yield 500
    prepare_inputs()
    sl = CL.getVar("userSliders")
    find(sl, "transp1").setOpenSndCtrl("9010:/t1")
    find(sl, "transp1").setOSCOut("127.0.0.1:9110:/t1out")
    find(sl, "gain2").setMidiCtl(20)
    frame = CL.getVar("userSamplers")[0].getSamplerFrame()
    frame.gainSlider.setOpenSndCtrl("9011:/g")
    frame.transpSlider.setMidiCtl(21)
    yield from bounce("chordmaker_oscmidi")
    open_module("Pitch/LooperMod.c5"); yield 500
    prepare_inputs()
    r = find(CL.getVar("userSliders"), "amrange")
    r.setOpenSndCtrl("9012:/amlo", side="left")
    r.setOpenSndCtrl("9012:/amhi", side="right")
    r.setOSCOut("127.0.0.1:9112:/amout", side="left")
    find(CL.getVar("userSliders"), "fmrange").setMidiCtl([22, 23])
    yield from bounce("loopermod_oscmidi")
    CL.setVar("useMidi", 0)

    # ---- 8. MIDI learn, interface side (no controller here: fake the engine's events)
    target = find(CL.getVar("userSliders"), "amspeed")
    try:
        import Resources.engine_client as ec
        ec._learn = {"widget": target, "range": False, "ctls": []}
        ec._onMidiCtl(33, 2)
        ec._onMidiCtl(34, 3)   # ignored, already learned
        rtarget = find(CL.getVar("userSliders"), "fmrange")
        ec._learn = {"widget": rtarget, "range": True, "ctls": []}
        ec._onMidiCtl(40, 1); ec._onMidiCtl(40, 1); ec._onMidiCtl(41, 1)
        results["midilearn"] = [target.getMidiCtl(), target.getMidiChannel(),
                                rtarget.getMidiCtl(), rtarget.getMidiChannel()]
    except ImportError:
        results["midilearn"] = "n/a (master)"
    yield 400

    # ---- 9. modules that fail to compile / to run
    bad = os.path.join(WORK, "Broken.c5")
    with open(bad, "w") as f:
        f.write("class Module(BaseModule):\n    def __init__(self):\n        BaseModule.__init__(self)\n        self.out = Sine(\n")
    before = len(problems)
    CL.getVar("mainFrame").onOpen(bad, False); yield 800
    results["compile_error"] = [p[:40] for p in problems[before:]]
    del problems[before:]
    results["compile_error_reloaded"] = os.path.basename(CL.getVar("currentCeciliaFile"))
    bad2 = os.path.join(WORK, "Crash.c5")
    with open(bad2, "w") as f:
        f.write("class Module(BaseModule):\n    def __init__(self):\n        BaseModule.__init__(self)\n        self.out = undefined_name\n\n"
                "Interface = [cgraph(name='env', label='Amp', func=[(0,1),(1,1)], col='blue1')]\n")
    CL.getVar("mainFrame").onOpen(bad2, False); yield 600
    before = len(problems)
    cp = CL.getControlPanel()
    cp.transportButtons.setPlay(True); cp.onPlayStop(True)
    yield 1200
    results["runtime_error"] = [p[:40] for p in problems[before:]]
    del problems[before:]
    results["runtime_error_reloaded"] = os.path.basename(CL.getVar("currentCeciliaFile"))
    results["runtime_error_running"] = engine_running()

    # ---- 10. record to file while playing
    open_module("Time/Granulator.c5"); yield 500
    prepare_inputs(total=1.5)
    cp = CL.getControlPanel()
    cp.gainSlider.SetValue(-60)
    rec = os.path.join(WORK, "rec.wav")
    cp.outputFilename = ""
    cp.onSelectOutputFilename = lambda: rec
    cp.transportButtons.setRecord(True)
    cp.onRec(True)
    t0 = time.time()
    yield 300
    while engine_running() and time.time() - t0 < 6:
        yield 50
    yield 400
    d = read_wav(rec)
    results["record"] = {"frames_ok": d.shape[0] > 48000, "nonsilent": bool(np.max(np.abs(d)) > 0)}
    log("record", results["record"])

def finish():
    results["problems"] = problems
    print("RESULT " + json.dumps(results, sort_keys=True), flush=True)
    os._exit(0)

def run():
    CL = C()
    CL.showErrorDialog = lambda title, msg: problems.append("dialog %s: %s" % (title, msg[:300]))
    engine = CL.getEngine() if hasattr(CL, "getEngine") else None
    if engine is not None and hasattr(engine, "testSeed"):
        engine.testSeed(1234)
    else:
        srv = audio_server()
        orig = srv.reinit
        def seeded():
            orig(); srv.server.setGlobalSeed(1234)
        srv.reinit = seeded
    gen = scenarios()
    def step():
        try:
            d = next(gen)
        except StopIteration:
            finish(); return
        except Exception:
            problems.append(traceback.format_exc())
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
