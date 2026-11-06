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
commands (AudioServer.COMMANDS in Resources/audio.py). LocalEngine runs the
engine in this process.

The engine reports back with events (clock, meters, automated widgets, MIDI
learn, spectrum, end of play). They arrive on the main thread through
uibridge and are applied to the widgets here, by name.
"""
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


class LocalEngine:
    """The engine, in this process. Each command is a method of the
    AudioServer (see AudioServer.COMMANDS)."""
    def __init__(self):
        from . import audio
        self.server = audio.AudioServer(_UiSink(), snapshot.enginePrefs())
        for name in audio.AudioServer.COMMANDS:
            setattr(self, name, getattr(self.server, name))


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
        time, m, s, c = payload
        grapher.cursorPanel.setTime(time)
        CeciliaLib.getVar("interface").controlPanel.setTime(time, m, s, c)


def _onNearEnd():
    if CeciliaLib.getControlPanel() is not None:
        CeciliaLib.getControlPanel().closeBounceToDiskDialog()


def _onEnd():
    CeciliaLib.stopCeciliaSound()


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
