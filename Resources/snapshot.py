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
What the audio engine needs to know to play the current module, as plain data
(dicts, lists, numbers, strings) read from the interface widgets.

The engine (Resources/audio.py) never reads widgets: the interface builds a
snapshot with build() and hands it to the engine's play() command.

    {
      "prefs":        enginePrefs(), audio settings and transport options
      "userInputs":   CeciliaVar["userInputs"] (sound files and their infos)
      "fileins":      {name: {"offset", "mode"}}             cfilein widgets
      "samplers":     {name: {"info", "mode", "offset", "paths",
                              "sliders": {param: slider}}}  csampler widgets
      "sliders":      {name: slider}       cslider, crange and csplitter
      "userSliders":  [name, ...]          sliders of the interface, in order
      "lines":        [{"name", "suffix", "data", "curved"}, ...] grapher lines
      "togglePopups": [{"name", "type", "value", "label"}, ...]
      "plugins":      [None or {"name", "params", "knobs": [knob, knob, knob]}]
    }

slider: {"value", "min", "max", "log", "play", "rec", "midi", "osc",
         "midictl", "midichnl", "openSndCtrl", "OSCOut", "path"}
(sampler sliders: "value", "min", "max", "log", "openSndCtrl", "OSCOut";
 plugin knobs: those of a slider plus "name", without the OSC entries)
"""
import Resources.CeciliaLib as CeciliaLib
from .constants import AUDIO_FILE_FORMATS

# CeciliaVar entries the engine reads, under the same names.
PREF_KEYS = ("sr", "bufferSize", "nchnls", "enableAudioInput", "audioHostAPI", "audioOutput",
             "audioInput", "defaultFirstInput", "defaultFirstOutput", "midiDeviceIn", "useMidi",
             "automaticMidiBinding", "DEBUG", "toDac", "totalTime", "startOffset", "globalFade",
             "outputFile", "showSpectrum")

SAMPLER_PARAMS = (("start", "loopInSlider"), ("dur", "loopOutSlider"), ("xfade", "loopXSlider"),
                  ("gain", "gainSlider"), ("pitch", "transpSlider"))


def enginePrefs():
    getVar = CeciliaLib.getVar
    prefs = {key: getVar(key) for key in PREF_KEYS}
    prefs["jackClient"] = getVar("jack").get("client", "cecilia5")
    prefs["currentCeciliaFile"] = getVar("currentCeciliaFile", unicode=True)
    prefs["fileFormat"] = AUDIO_FILE_FORMATS[getVar("audioFileType")]
    prefs["sampleType"] = CeciliaLib.getExportSampleType()
    return prefs


def _slider(w):
    return {
        "value": w.getValue(),
        "min": w.getMinValue(),
        "max": w.getMaxValue(),
        "log": w.getLog(),
        "play": w.getPlay(),
        "rec": w.getRec(),
        "midi": w.getWithMidi(),
        "osc": w.getWithOSC() if hasattr(w, "getWithOSC") else False,
        "midictl": w.getMidiCtl(),
        "midichnl": w.getMidiChannel(),
        "openSndCtrl": getattr(w, "openSndCtrl", None),
        "OSCOut": getattr(w, "OSCOut", None),
        "path": w.getPath(),
    }


def _samplerSlider(w):
    return {
        "value": w.getValue(),
        "min": w.getMinValue(),
        "max": w.getMaxValue(),
        "log": w.getLog(),
        "openSndCtrl": w.openSndCtrl,
        "OSCOut": w.OSCOut,
    }


def _knob(k):
    d = _slider(k)
    d["name"] = k.getName()
    return d


def plugin(p):
    """Snapshot of one plugin widget (or None for an empty slot)."""
    if p is None:
        return None
    return {"name": p.getName(), "params": p.getParams(), "knobs": [_knob(k) for k in p.getKnobs()]}


def build():
    getVar = CeciliaLib.getVar
    controlPanel = CeciliaLib.getControlPanel()

    fileins = {}
    for filein in controlPanel.getCfileinList():
        fileins[filein.name] = {"offset": filein.getOffset(), "mode": filein.getMode()}

    samplers = {}
    for sampler in getVar("userSamplers"):
        frame = sampler.getSamplerFrame()
        samplers[sampler.name] = {
            "info": sampler.getSamplerInfo(),
            "mode": sampler.mode,
            "offset": sampler.getOffset(),
            "paths": [s.getPath() for s in sampler.getSamplerSliders()],
            "sliders": {param: _samplerSlider(getattr(frame, attr)) for param, attr in SAMPLER_PARAMS},
        }

    lines = []
    plotterLines = getVar("grapher").plotter.getData()
    for line in plotterLines:
        lines.append({"name": line.name, "suffix": line.suffix,
                      "data": [tuple(p) for p in line.getData()], "curved": line.getCurved()})

    sliders = {}
    userSliders = []
    for w in getVar("userSliders"):
        sliders[w.name] = _slider(w)
        userSliders.append(w.name)
    # sliders drawn in the grapher (the engine looks these up through their line)
    for line in plotterLines:
        w = line.slider
        if (w is not None and line.suffix != "sampler" and w.name not in sliders and
                getattr(w, "widget_type", None) in ("slider", "range", "splitter")):
            sliders[w.name] = _slider(w)

    togglePopups = []
    for w in getVar("userTogglePopups"):
        togglePopups.append({"name": w.name, "type": w.type, "value": w.getValue(),
                             "label": w.getLabel() if hasattr(w, "getLabel") else None})

    return {
        "prefs": enginePrefs(),
        "userInputs": CeciliaLib.deepCopy(getVar("userInputs")),
        "fileins": fileins,
        "samplers": samplers,
        "sliders": sliders,
        "userSliders": userSliders,
        "lines": lines,
        "togglePopups": togglePopups,
        "plugins": [plugin(p) for p in getVar("plugins")],
    }
