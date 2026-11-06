"""
Copyright 2019 iACT, Universite de Montreal,
Jean Piche, Olivier Belanger, Jean-Michel Dumas

This file is part of Cecilia 5.

Cecilia 5 is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Cecilia 5 is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Cecilia 5.  If not, see <http://www.gnu.org/licenses/>.
"""

"""
The interface's side of the audio engine.

CeciliaLib.getEngine() returns an engine object whose methods are the engine
commands (AudioServer.COMMANDS in Resources/audio.py). RemoteEngine runs the
engine in its own process (Resources/engine_process.py), so audio work never
blocks the interface; LocalEngine runs it in this process (CECILIA_ENGINE=local,
for debugging).

The engine reports back with events (clock, meters, automated widgets, MIDI
learn, spectrum, end of play). They arrive on the main thread through
uibridge and are applied to the widgets here, by name.
"""
import itertools
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from multiprocessing.connection import Listener

import wx

import Resources.CeciliaLib as CeciliaLib
from . import uibridge, snapshot


class _UiSink:
    """Engine events -> main thread (see uibridge)."""
    def latest(self, key, *args):
        uibridge.latest(key, _dispatch, key, args)

    def event(self, name, *args):
        uibridge.event(_dispatch, (name,), args)

    def clear(self):
        uibridge.clear_latest()


def createEngine():
    """The engine the interface uses: in its own process, unless the
    CECILIA_ENGINE environment variable is "local" (for debugging)."""
    if os.environ.get("CECILIA_ENGINE", "").lower() == "local":
        return LocalEngine()
    try:
        return RemoteEngine()
    except Exception:
        traceback.print_exc()
        print("Cecilia: the audio engine process could not start, running it in the interface.")
        return LocalEngine()


class LocalEngine:
    """The engine, in this process. Each command is a method of the
    AudioServer (see AudioServer.COMMANDS)."""
    def __init__(self):
        from . import audio
        self.server = audio.AudioServer(_UiSink(), snapshot.enginePrefs())
        for name in audio.AudioServer.COMMANDS:
            if name != "stop":
                setattr(self, name, getattr(self.server, name))

        self.stopCount = 0

    def playAsync(self, snap, timer, rec, done):
        """play(), then done(ok, error)."""
        ok, error = self.server.play(snap, timer, rec)
        done(ok, error)

    def stop(self):
        self.stopCount += 1
        self.server.stop()

    def abort(self):
        """Stops at once (Cancel during a bounce)."""
        self.stop()

    def close(self):
        if self.server.isRunning():
            self.server.stop()


class EngineError(Exception):
    pass


class RemoteEngine:
    """The engine, in its own process (Resources/engine_process.py).

    Same commands as LocalEngine. Commands whose result the interface needs
    (SYNC) wait for the engine's answer; the others are sent and forgotten.
    isRunning() answers from the last state the engine reported, without
    asking it.

    If the engine process dies, it is started again with the same settings
    and module, and the interface is told (see _onEngineDied)."""
    SYNC = {"openModule": 60, "play": 60, "stop": 5, "writeAutomation": 30, "isBooted": 5}
    START_TIMEOUT = 30

    def __init__(self):
        from .audio import AudioServer
        COMMANDS = AudioServer.COMMANDS
        self._cfg = snapshot.enginePrefs()
        self._modulePath = None   # last module opened (re-opened after a restart)
        self._seed = None
        self._lock = threading.Lock()
        self._ids = itertools.count()
        self._generation = 0
        self._closing = False
        self._proc = None
        self._running = False
        self._callbacks = {}      # command id -> function called with its result
        self.stopCount = 0        # stops asked so far (see playAsync)
        for name in COMMANDS:
            if name in ("isRunning", "play", "configure", "openModule", "testSeed", "stop"):
                continue
            setattr(self, name, self._command(name))
        self._start()

    ###### process ######
    def _start(self):
        from . import engine_process
        self._generation += 1
        generation = self._generation
        self._running = False
        self._replies = queue.Queue()
        key = os.urandom(32)
        listener = Listener(authkey=key)
        env = dict(os.environ)
        env["CECILIA_ENGINE_KEY"] = key.hex()
        env["CECILIA_SAMPLE_PRECISION"] = CeciliaLib.getVar("samplePrecision")
        if os.environ.get("CECILIA_ENGINE_EXECUTABLE"):
            # test tools: run the engine of an app bundle
            cmd = [os.environ["CECILIA_ENGINE_EXECUTABLE"], engine_process.ENGINE_ARG, listener.address]
        elif getattr(sys, "frozen", False):
            cmd = [sys.executable, engine_process.ENGINE_ARG, listener.address]
        else:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env["PYTHONPATH"] = os.pathsep.join([root] + [p for p in [env.get("PYTHONPATH")] if p])
            cmd = [sys.executable, "-m", "Resources.engine_process", listener.address]
        self._proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL)

        conns = []
        def accept():
            try:
                conns.append(listener.accept())
                conns.append(listener.accept())
            except Exception:
                pass
        t = threading.Thread(target=accept, daemon=True)
        t.start()
        deadline = time.monotonic() + self.START_TIMEOUT
        while len(conns) < 2 and time.monotonic() < deadline and self._proc.poll() is None:
            t.join(0.05)
        listener.close()
        if len(conns) < 2:
            self._proc.kill()
            raise EngineError("The audio engine process did not start.")
        self._commands, messages = conns
        self._commands.send(self._cfg)
        threading.Thread(target=self._read, args=(messages, generation, self._proc),
                         name="engine-reader", daemon=True).start()

    def _restart(self):
        self._kill()
        self._start()
        if self._seed is not None:
            self._send("testSeed", (self._seed,), {}, False)
        if self._modulePath is not None:
            self._call("openModule", self._modulePath)

    def _kill(self):
        self._generation += 1  # the old reader won't report this death
        self._dropCallbacks()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(2)
            except Exception:
                pass
        self._running = False

    def _alive(self):
        return self._proc is not None and self._proc.poll() is None

    def _read(self, conn, generation, proc):
        """Reader thread: engine messages -> main thread (through uibridge)."""
        replies = self._replies
        while True:
            try:
                msg = conn.recv()
            except (EOFError, OSError):
                break
            except Exception:
                traceback.print_exc()
                break
            if generation != self._generation:
                return
            kind = msg[0]
            if kind == "latest":
                uibridge.latest(msg[1], _dispatch, msg[1], msg[2])
            elif kind == "event":
                uibridge.event(_dispatch, (msg[1],), msg[2])
            elif kind == "clear":
                uibridge.clear_latest()
            elif kind == "state":
                self._running = msg[1]
            elif kind == "reply":
                self._running = msg[4]
                callback = self._callbacks.pop(msg[1], None)
                if callback is None:
                    replies.put(msg)
                else:
                    ok, result = msg[2], msg[3]
                    if not ok:  # the command raised
                        result = (False, "The audio engine failed:\n%s" % result)
                    uibridge.event(callback, *result)
        try:
            code = proc.wait(2)
        except Exception:
            code = None
        if generation == self._generation and not self._closing:
            self._running = False
            self._dropCallbacks()
            uibridge.event(_onEngineDied, code)

    ###### commands ######
    def _send(self, name, args, kwargs, wantReply):
        ident = next(self._ids)
        with self._lock:
            self._commands.send((ident, name, args, kwargs, wantReply))
        return ident

    def _call(self, name, *args, **kwargs):
        """Sends a command and waits for its result (see SYNC)."""
        if not self._alive():
            self._restart()
        replies = self._replies
        try:
            ident = self._send(name, args, kwargs, True)
        except (OSError, EOFError):
            raise EngineError("The audio engine is not responding.")
        deadline = time.monotonic() + self.SYNC.get(name, 30)
        while True:
            try:
                msg = replies.get(timeout=0.05)
            except queue.Empty:
                if not self._alive():
                    raise EngineError("The audio engine stopped.")
                if time.monotonic() > deadline:
                    raise EngineError("The audio engine did not answer (%s)." % name)
                continue
            if msg[1] != ident:
                continue  # answer to a command that timed out
            ok, result = msg[2], msg[3]
            if not ok:
                raise EngineError("The audio engine failed (%s):\n%s" % (name, result))
            return result

    def _command(self, name):
        if name in self.SYNC:
            def method(*args, **kwargs):
                try:
                    return self._call(name, *args, **kwargs)
                except EngineError:
                    traceback.print_exc()  # the engine is restarted on the next command
                    return None
        else:
            def method(*args, **kwargs):
                if self._alive():
                    try:
                        self._send(name, args, kwargs, False)
                    except (OSError, EOFError):
                        pass
        method.__name__ = name
        return method

    def _dropCallbacks(self):
        """The engine went away: commands waiting for a result get
        (False, None)."""
        callbacks, self._callbacks = self._callbacks, {}
        for callback in callbacks.values():
            uibridge.event(callback, False, None)

    def isRunning(self):
        # A play that hasn't answered yet counts as running.
        return self._running or bool(self._callbacks)

    def configure(self, cfg):
        self._cfg.update(cfg)
        if self._alive():
            self._send("configure", (cfg,), {}, False)

    def testSeed(self, seed):
        self._seed = seed
        if self._alive():
            self._send("testSeed", (seed,), {}, False)

    def openModule(self, filepath):
        try:
            result = self._call("openModule", filepath)
        except EngineError as e:
            return False, str(e)
        self._modulePath = filepath if result[0] else None
        return result

    def play(self, snap, timer=True, rec=False):
        self._cfg.update(snap["prefs"])
        try:
            return self._call("play", snap, timer, rec)
        except EngineError as e:
            self._abortAfterError()
            return False, str(e)

    def playAsync(self, snap, timer, rec, done):
        """Starts playing without waiting for the engine: done(ok, error) is
        called on the main thread when it has started (or failed; error is
        None if the engine stopped)."""
        self._cfg.update(snap["prefs"])
        if not self._alive():
            try:
                self._restart()
            except EngineError as e:
                done(False, str(e))
                return
        ident = next(self._ids)
        self._callbacks[ident] = done
        try:
            with self._lock:
                self._commands.send((ident, "play", (snap, timer, rec), {}, True))
        except (OSError, EOFError):
            self._callbacks.pop(ident, None)
            done(False, None)

    def stop(self):
        self.stopCount += 1
        if not self.isRunning():
            # Still tell the engine: it may have started on its own (MIDI learn).
            if self._alive():
                self._send("stop", (), {}, False)
            return
        try:
            self._call("stop")
        except EngineError:
            self.abort()

    def abort(self):
        """Stops at once, by restarting the engine: an offline render keeps
        the engine too busy to answer quickly (Cancel during a bounce)."""
        self._restart()

    def _abortAfterError(self):
        try:
            self._restart()
        except EngineError:
            traceback.print_exc()

    def close(self):
        """Quitting: stops the engine process."""
        self._closing = True
        if self._alive():
            try:
                self._send("_quit", (), {}, False)
                self._proc.wait(2)
            except Exception:
                pass
        if self._alive():
            self._proc.kill()


def _onEngineDied(code):
    """The engine process died (main thread): restart it and tell the user."""
    engine = CeciliaLib.getEngine()
    if not isinstance(engine, RemoteEngine) or engine._alive():
        return  # already restarted by a command
    try:
        engine._restart()
        restarted = "It has been restarted."
    except EngineError:
        traceback.print_exc()
        restarted = "It could not be restarted: try to play again."
    controlPanel = CeciliaLib.getControlPanel()
    if controlPanel is not None:
        controlPanel.closeBounceToDiskDialog()
    if CeciliaLib.getVar("spectrumFrame") is not None:
        try:
            CeciliaLib.getVar("spectrumFrame").onClose()
        except Exception:
            pass
        CeciliaLib.setVar("spectrumFrame", None)
    CeciliaLib.resetControls()
    CeciliaLib.showErrorDialog("Audio engine stopped",
                               "The audio engine stopped unexpectedly (exit code %s). %s" % (code, restarted))


###### Events from the engine ######

def _dispatch(key, args):
    _HANDLERS[key[0]](*(key[1:] + tuple(args)))


def _onClock(payload):
    grapher = CeciliaLib.getVar("grapher")
    if payload == "cursor":
        if grapher is not None:
            grapher.cursorPanel.setTime(CeciliaLib.getVar("startOffset"))
    elif payload == "reset":
        grapher.cursorPanel.setTime(CeciliaLib.getVar("startOffset"))
        CeciliaLib.getVar("interface").controlPanel.setTime(0, 0, 0, 0)
    else:
        global renderTime
        t, m, s, c = payload
        grapher.cursorPanel.setTime(t)
        controlPanel = CeciliaLib.getVar("interface").controlPanel
        controlPanel.setTime(t, m, s, c)
        if not CeciliaLib.getVar("toDac"):
            # bounce/batch: the clock is the progress of the render
            renderTime = t
            total = CeciliaLib.getVar("totalTime")
            if total > 0:
                controlPanel.setBounceProgress(t / total)


renderTime = 0.0  # position of the offline render in progress (seconds)


def _onNearEnd():
    if CeciliaLib.getControlPanel() is not None:
        CeciliaLib.getControlPanel().closeBounceToDiskDialog()


def _onEnd():
    CeciliaLib.stopCeciliaSound()
    if not CeciliaLib.getVar("toDac"):
        # end of a bounce/batch render
        controlPanel = CeciliaLib.getControlPanel()
        if controlPanel is not None:
            controlPanel.closeBounceToDiskDialog()
        if CeciliaLib.getVar("grapher") is not None and CeciliaLib.getVar("interface") is not None:
            _onClock("reset")


def _onMeter(args, peak):
    controlPanel = CeciliaLib.getControlPanel()
    if controlPanel is not None:
        controlPanel.vuMeter.setRms(*args, peak=peak)


def _findSlider(name):
    for slider in CeciliaLib.getVar("userSliders"):
        if slider.name == name:
            return slider
    for line in CeciliaLib.getVar("grapher").plotter.getData():
        if line.slider is not None and line.suffix != "sampler" and line.slider.name == name:
            return line.slider
    return None


def _onSlider(name, value):
    slider = _findSlider(name)
    if slider is not None:
        slider.setValue(value)


def _onSliderOne(name, which, value):
    slider = _findSlider(name)
    if slider is not None:
        slider.setOneValue(value, which)


def _onSampler(name, param, value, sendIfHidden):
    for sampler in CeciliaLib.getVar("userSamplers"):
        if sampler.name == name:
            attr = dict(snapshot.SAMPLER_PARAMS)[param]
            widget = getattr(sampler.getSamplerFrame(), attr)
            widget.setValue(value)
            # A hidden slider doesn't redraw, so it wouldn't send its value.
            if sendIfHidden and not widget.slider.IsShownOnScreen():
                widget.sendValue(value)
            return


def _onPluginKnob(order, which, name, value):
    plugin = CeciliaLib.getVar("plugins")[order]
    if plugin is not None and plugin.getName() == name:
        plugin.getKnobs()[which].setValue(value)


def _onGainCtl7(value):
    controlPanel = CeciliaLib.getControlPanel()
    if controlPanel is not None:
        controlPanel.gainSlider.SetValue(value)


def _onSpectrum(points):
    if _spectrum is not None and _spectrum.function is not None and \
       CeciliaLib.getVar("spectrumFrame") is not None:
        _spectrum.function(points)


###### MIDI learn ######

_learn = None


def startMidiLearn(widget, rangeSlider=False):
    """Assigns the next MIDI controller(s) moved to widget (two controllers
    for a range slider)."""
    global _learn
    _learn = {"widget": widget, "range": rangeSlider, "ctls": []}
    engine = CeciliaLib.getEngine()
    engine.configure(snapshot.enginePrefs())  # current audio/MIDI devices
    engine.midiLearn()


def _onMidiCtl(number, midichnl):
    global _learn
    if _learn is None:
        return  # already learned; later controller moves are ignored
    widget = _learn["widget"]
    if not _learn["range"]:
        widget.setMidiChannel(midichnl)
        widget.setMidiCtl(number)
    else:
        ctls = _learn["ctls"]
        if [number, midichnl] in ctls:
            return
        ctls.append([number, midichnl])
        if len(ctls) < 2:
            return
        widget.setMidiChannel([ctls[0][1], ctls[1][1]])
        widget.setMidiCtl([ctls[0][0], ctls[1][0]])
    _learn = None
    wx.CallLater(250, CeciliaLib.getEngine().stop)


###### Spectrum ######

_spectrum = None


class SpectrumProxy:
    """Stands in for pyo's Spectrum object in the spectrum window
    (PyoGuiSpectrum.setAnalyzer). Settings are forwarded to the engine, which
    sends back the points to draw."""
    def __init__(self, nchnls, sr):
        global _spectrum
        _spectrum = self
        self.function = None
        self._nchnls = nchnls
        self._sr = sr

    def __len__(self):
        return self._nchnls

    def setFunction(self, function):
        self.function = function

    def _forward(name):
        def method(self, *args):
            CeciliaLib.getEngine().spectrumCall(name, *args)
        return method

    setLowFreq = _forward("setLowFreq")
    setHighFreq = _forward("setHighFreq")
    setFscaling = _forward("setFscaling")
    setMscaling = _forward("setMscaling")
    setWidth = _forward("setWidth")
    setHeight = _forward("setHeight")
    setGain = _forward("setGain")
    refreshView = _forward("refreshView")

    def setLowbound(self, x):
        """Returns the low frequency in Hz, as Spectrum.setLowbound."""
        x = min(max(x, 0.0), 0.5)
        CeciliaLib.getEngine().spectrumCall("setLowbound", x)
        return x * self._sr

    def setHighbound(self, x):
        x = min(max(x, 0.0), 0.5)
        CeciliaLib.getEngine().spectrumCall("setHighbound", x)
        return x * self._sr

    @property
    def wintype(self):
        return None

    @wintype.setter
    def wintype(self, x):
        CeciliaLib.getEngine().spectrumCall("wintype", x)

    @property
    def size(self):
        return None

    @size.setter
    def size(self, x):
        CeciliaLib.getEngine().spectrumCall("size", x)


_HANDLERS = {
    "clock": _onClock,
    "near_end": _onNearEnd,
    "end": _onEnd,
    "meter": _onMeter,
    "slider": _onSlider,
    "slider_one": _onSliderOne,
    "sampler": _onSampler,
    "plugin_knob": _onPluginKnob,
    "gain_ctl7": _onGainCtl7,
    "midi_ctl": _onMidiCtl,
    "spectrum": _onSpectrum,
}
