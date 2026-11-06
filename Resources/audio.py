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

import os, math, copy, time, traceback, sys, random
from .constants import *
from .API_interface import *

# Set by the interface when it starts the engine process (engine_client.py).
_precision = os.environ.get("CECILIA_SAMPLE_PRECISION")
if _precision is None:
    from .Variables import CeciliaVar as _prefs
    _precision = _prefs["samplePrecision"]

if _precision == '64 bit':
    from pyo64 import *
else:
    from pyo import *

# The audio engine.
#
# Nothing in this file touches the wx interface. The interface describes the
# module to play with a snapshot (plain data, see Resources/snapshot.py) and
# drives the engine through the commands of AudioServer (see
# Resources/engine_client.py). Everything the engine has to tell the
# interface goes through the event sink given to AudioServer:
#
#   sink.latest(key, *args)  a value to display, only the newest one matters
#   sink.event(name, *args)  something that happened, delivered in order
#   sink.clear()             drop pending values (after stop)

_engine = None    # the AudioServer
_snapshot = None  # snapshot of the module being built


def _cfg(key):
    return _engine.cfg[key]


def _emit(key, *args):
    _engine.sink.latest(key, *args)


def _event(name, *args):
    _engine.sink.event(name, *args)


_NO_LINE = {"name": "", "suffix": "", "data": [], "curved": False}


def _line(name, suffix=None):
    """First grapher line with this name (and suffix), as in the plotter."""
    for line in _snapshot["lines"]:
        if line["name"] == name and (suffix is None or line["suffix"] == suffix):
            return line
    return _NO_LINE


def _lineData(line):
    return [tuple(x) for x in line["data"]]


def deepCopy(ori):
    """Same as CeciliaLib.deepCopy: copies nested dicts/lists, tuples become lists."""
    if type(ori) is dict:
        return {key: deepCopy(ori[key]) for key in ori}
    elif type(ori) in (list, tuple):
        return [deepCopy(x) for x in ori]
    return ori


class CeciliaFilein:
    def __init__(self, parent, name):
        self.parent = parent
        self.name = name
        info = _snapshot["userInputs"][name]
        chnls = _cfg("nchnls")
        offset = _snapshot["fileins"][name]["offset"]
        mode = _snapshot["fileins"][name]["mode"]
        if mode == 0:
            snd_chnls = info['nchnls' + self.name]
            if snd_chnls == 1:
                self.table = SndTable([info['path'] for i in range(chnls)], start=info["off" + self.name])
            else:
                self.table = SndTable(info['path'], start=info["off" + self.name])
        else:
            self.table = NewTable(length=offset, chnls=chnls, feedback=0.0)
            self.livein = Input(chnl=[x for x in range(chnls)], mul=0.7)
            self.filltabrec = TableRec(self.livein, self.table, fadetime=0.05).play()

    def sig(self):
        return self.table

class CeciliaSampler:
    def __init__(self, parent, name, user_pitch, user_amp):
        self.type = "sampler"
        self.parent = parent
        self.baseModule = parent
        self.name = name
        self.user_pitch = user_pitch
        self.user_amp = user_amp
        info = _snapshot["userInputs"][name]
        totalTime = _cfg("totalTime")
        chnls = _cfg("nchnls")
        self.sampler = _snapshot["samplers"][name]
        sinfo = self.sampler["info"]
        self.mode = self.sampler["mode"]

        self.start_play, self.start_midi = False, False
        self.dur_play, self.dur_midi = False, False
        self.xfade_play, self.xfade_midi = False, False
        self.gain_play, self.gain_midi = False, False
        self.pitch_play, self.pitch_midi = False, False

        if self.mode != 1:
            graph_lines = {}
            for line in _snapshot["lines"]:
                if line["name"].startswith(self.name):
                    graph_lines[line["name"]] = line

            paths = self.sampler["paths"]

            ################ start ################
            start_init, self.start_play, self.start_rec = sinfo['loopIn'][0], sinfo['loopIn'][1], sinfo['loopIn'][2]
            try:
                self.start_midi, start_midictl, start_midichnl = sinfo['loopIn'][3], sinfo['loopIn'][4], sinfo['loopIn'][5]
                self.start_mini, self.start_maxi = sinfo['loopIn'][6], sinfo['loopIn'][7]
                self.start_osc = sinfo['loopIn'][8]
            except:
                self.start_midi, start_midictl, start_midichnl, self.start_mini, self.start_maxi = False, None, 1, 0, 1
                self.start_osc = None
            line = graph_lines[self.name + 'start']
            curved = line["curved"]
            if curved:
                self.start_table = CosTable()
            else:
                self.start_table = LinTable()
            if not self.start_play and not self.start_midi:
                self.start = SigTo(value=start_init, time=0.025, init=start_init)
            if self.start_play:
                data = _lineData(line)
                self.setGraph('start', data)
                self.start = TableRead(self.start_table, freq=1.0 / totalTime).play()
            elif self.start_midi:
                self.start = Midictl(start_midictl, self.start_mini, self.start_maxi, start_init, start_midichnl)
            elif self.start_osc is not None:
                self.baseModule._addOpenSndCtrlWidget(self.start_osc[0], self.start_osc[1], self, name='start')
            if self.start_rec:
                self.start_record = ControlRec(self.start, filename=paths[0], rate=1000, dur=totalTime).play()

            ################  dur  ################
            dur_init, self.dur_play, self.dur_rec = sinfo['loopOut'][0], sinfo['loopOut'][1], sinfo['loopOut'][2]
            try:
                self.dur_midi, dur_midictl, dur_midichnl = sinfo['loopOut'][3], sinfo['loopOut'][4], sinfo['loopOut'][5]
                self.dur_mini, self.dur_maxi = sinfo['loopOut'][6], sinfo['loopOut'][7]
                self.dur_osc = sinfo['loopOut'][8]
            except:
                self.dur_midi, dur_midictl, dur_midichnl, self.dur_mini, self.dur_maxi = False, None, 1, 0, 1
                self.dur_osc = None
            line = graph_lines[self.name + 'end']
            curved = line["curved"]
            if curved:
                self.dur_table = CosTable()
            else:
                self.dur_table = LinTable()
            if not self.dur_play and not self.dur_midi:
                self.dur = SigTo(value=dur_init, time=0.025, init=dur_init)
            if self.dur_play:
                data = _lineData(line)
                self.setGraph('end', data)
                self.dur = TableRead(self.dur_table, freq=1.0 / totalTime).play()
            elif self.dur_midi:
                self.dur = Midictl(dur_midictl, self.dur_mini, self.dur_maxi, dur_init, dur_midichnl)
            elif self.dur_osc is not None:
                self.baseModule._addOpenSndCtrlWidget(self.dur_osc[0], self.dur_osc[1], self, name='dur')
            if self.dur_rec:
                self.dur_record = ControlRec(self.dur, filename=paths[1], rate=1000, dur=totalTime).play()

            ################ xfade ################
            xfade_init, self.xfade_play, self.xfade_rec = sinfo['loopX'][0], sinfo['loopX'][1], sinfo['loopX'][2]
            try:
                self.xfade_midi, xfade_midictl, xfade_midichnl = sinfo['loopX'][3], sinfo['loopX'][4], sinfo['loopX'][5]
                self.xfade_osc = sinfo['loopX'][6]
            except:
                self.xfade_midi, xfade_midictl, xfade_midichnl = False, None, 1
                self.xfade_osc = None
            line = graph_lines[self.name + 'xfade']
            curved = line["curved"]
            if curved:
                self.xfade_table = CosTable()
            else:
                self.xfade_table = LinTable()
            if not self.xfade_play and not self.xfade_midi:
                self.xfade = SigTo(value=xfade_init, time=0.025, init=xfade_init)
            if self.xfade_play:
                data = _lineData(line)
                self.setGraph('xfade', data)
                self.xfade = TableRead(self.xfade_table, freq=1.0 / totalTime).play()
            elif self.xfade_midi:
                self.xfade = Midictl(xfade_midictl, 0, 50, xfade_init, xfade_midichnl)
            elif self.xfade_osc is not None:
                self.baseModule._addOpenSndCtrlWidget(self.xfade_osc[0], self.xfade_osc[1], self, name='xfade')
            if self.xfade_rec:
                self.xfade_record = ControlRec(self.xfade, filename=paths[2], rate=1000, dur=totalTime).play()

            ################ gain ################
            gain_init, self.gain_play, self.gain_rec = sinfo['gain'][0], sinfo['gain'][1], sinfo['gain'][2]
            try:
                self.gain_midi, gain_midictl, gain_midichnl = sinfo['gain'][3], sinfo['gain'][4], sinfo['gain'][5]
                self.gain_osc = sinfo['gain'][6]
            except:
                self.gain_midi, gain_midictl, gain_midichnl = False, None, 1
                self.gain_osc = None
            line = graph_lines[self.name + 'gain']
            curved = line["curved"]
            if curved:
                self.gain_table = CosTable()
            else:
                self.gain_table = LinTable()
            if not self.gain_play and not self.gain_midi:
                self.gain_in = SigTo(value=gain_init, time=0.025, init=gain_init)
            if self.gain_play:
                data = _lineData(line)
                self.setGraph('gain', data)
                self.gain_in = TableRead(self.gain_table, freq=1.0 / totalTime).play()
            elif self.gain_midi:
                self.gain_in = Midictl(gain_midictl, -48, 18, gain_init, gain_midichnl)
            elif self.gain_osc is not None:
                self.baseModule._addOpenSndCtrlWidget(self.gain_osc[0], self.gain_osc[1], self, name='gain')
            if self.gain_rec:
                self.gain_record = ControlRec(self.gain_in, filename=paths[3], rate=1000, dur=totalTime).play()
            self.gain = Pow(10, self.gain_in * 0.05, mul=self.user_amp)

            ################ pitch ################
            pitch_init, self.pitch_play, self.pitch_rec = sinfo['transp'][0], sinfo['transp'][1], sinfo['transp'][2]
            try:
                self.pitch_midi, pitch_midictl, pitch_midichnl = sinfo['transp'][3], sinfo['transp'][4], sinfo['transp'][5]
                self.pitch_osc = sinfo['transp'][6]
            except:
                self.pitch_midi, pitch_midictl, pitch_midichnl = False, None, 1
                self.pitch_osc = None
            line = graph_lines[self.name + 'trans']
            curved = line["curved"]
            if curved:
                self.pitch_table = CosTable()
            else:
                self.pitch_table = LinTable()
            if not self.pitch_play and not self.pitch_midi:
                self.pitch_in = SigTo(value=pitch_init, time=0.025, init=pitch_init)
            if self.pitch_play:
                data = _lineData(line)
                self.setGraph('trans', data)
                self.pitch_in = TableRead(self.pitch_table, freq=1.0 / totalTime).play()
            elif self.pitch_midi:
                self.pitch_in = Midictl(pitch_midictl, -48, 48, pitch_init, pitch_midichnl)
            elif self.pitch_osc is not None:
                self.baseModule._addOpenSndCtrlWidget(self.pitch_osc[0], self.pitch_osc[1], self, name='pitch')
            if self.pitch_rec:
                self.pitch_record = ControlRec(self.pitch_in, filename=paths[4], rate=1000, dur=totalTime).play()
            self.pitch = Pow(1.0594630943593, self.pitch_in, self.user_pitch)
            if _cfg("automaticMidiBinding") and _cfg("useMidi"):
                self.checkNoteIn = Notein(poly=1, scale=0, first=0, last=120)
                self.onNewNote = Change(self.checkNoteIn["pitch"])
                self.noteTrigFunc = TrigFunc(self.onNewNote, self.newMidiPitch)

            if self.mode == 0:
                self.table = SndTable(info['path'], start=info["off" + self.name])
            elif self.mode == 2:
                self.table = NewTable(length=self.sampler["offset"], chnls=chnls)
                self.livein = Input(chnl=[x for x in range(chnls)], mul=0.7)
                self.filltabrec = TableRec(self.livein, self.table).play()
            elif self.mode == 3:
                self.table = NewTable(length=self.sampler["offset"], chnls=chnls)
                self.table2 = NewTable(length=self.sampler["offset"], chnls=chnls)
                self.livein = Input(chnl=[x for x in range(chnls)], mul=0.7)
                self.rectrig = Metro(time=self.sampler["offset"], poly=2).play()
                self.tmprec1 = TrigTableRec(self.livein, self.rectrig[0], self.table)
                self.tmprec2 = TrigTableRec(self.livein, self.rectrig[1], self.table2)
                self.morphind = Counter(self.rectrig.mix(1), min=0, max=2, dir=0)
                self.interp = SigTo(self.morphind, time=0.1)

            self.pitch_rnd = [x for x in self.parent.polyphony_spread for y in range(len(self.table))]
            if self.mode != 3:
                self.looper = Looper(table=self.table,
                                            pitch=self.pitch * self.pitch_rnd,
                                            start=self.start,
                                            dur=self.dur,
                                            xfade=self.xfade,
                                            mode=sinfo['loopMode'],
                                            xfadeshape=sinfo['xfadeshape'],
                                            startfromloop=sinfo['startFromLoop'],
                                            interp=4,
                                            autosmooth=True,
                                            mul=self.gain)
                self.mix = Mix(self.looper, voices=chnls, mul=self.parent.polyphony_scaling)
            else:
                self.looper = Looper(table=self.table,
                                            pitch=self.pitch * self.pitch_rnd,
                                            start=self.start,
                                            dur=self.dur,
                                            xfade=self.xfade,
                                            mode=sinfo['loopMode'],
                                            xfadeshape=sinfo['xfadeshape'],
                                            startfromloop=sinfo['startFromLoop'],
                                            interp=4,
                                            autosmooth=True,
                                            mul=self.gain)
                self.looper2 = Looper(table=self.table2,
                                            pitch=self.pitch * self.pitch_rnd,
                                            start=self.start,
                                            dur=self.dur,
                                            xfade=self.xfade,
                                            mode=sinfo['loopMode'],
                                            xfadeshape=sinfo['xfadeshape'],
                                            startfromloop=sinfo['startFromLoop'],
                                            interp=4,
                                            autosmooth=True,
                                            mul=self.gain)
                self.loopinterp = Interp(self.looper, self.looper2, self.interp)
                self.mix = Mix(self.loopinterp, voices=chnls, mul=self.parent.polyphony_scaling)

        else:
            self.mix = Input(chnl=[x for x in range(chnls)], mul=0.7)

    def setValueFromOSC(self, val, name):
        # audio thread
        if self.mode != 1:
            if name == 'start':
                val = rescale(val, ymin=self.start_mini, ymax=self.start_maxi)
            elif name == 'dur':
                val = rescale(val, ymin=self.dur_mini, ymax=self.dur_maxi)
            elif name == 'xfade':
                val = rescale(val, ymin=0, ymax=50)
            elif name == 'gain':
                val = rescale(val, ymin=-48, ymax=18)
            elif name == 'pitch':
                val = rescale(val, ymin=-48, ymax=48)
            else:
                return
            # The interface shows the value, and passes it back to the
            # engine when the sampler window is hidden.
            _emit(("sampler", self.name, name), val, True)

    def getWidget(self, name):
        return self.sampler["sliders"][name]

    def updateWidgets(self):
        if self.mode != 1:
            if self.start_midi and not self.start_play:
                _emit(("sampler", self.name, 'start'), self.start.get(), False)
            if self.dur_midi and not self.dur_play:
                _emit(("sampler", self.name, 'dur'), self.dur.get(), False)
            if self.xfade_midi and not self.xfade_play:
                _emit(("sampler", self.name, 'xfade'), self.xfade.get(), False)
            if self.gain_midi and not self.gain_play:
                _emit(("sampler", self.name, 'gain'), self.gain_in.get(), False)
            if self.pitch_midi and not self.pitch_play:
                _emit(("sampler", self.name, 'pitch'), self.pitch_in.get(), False)

    def newMidiPitch(self):
        # audio thread
        if not self.pitch_midi:
            _emit(("sampler", self.name, 'pitch'), self.checkNoteIn.get() - 60, True)

    def setGraph(self, which, func):
        if self.mode != 1:
            totalTime = _cfg("totalTime")
            func = [(int(x / totalTime * 8192), y) for x, y in func]
            if which.endswith('start'):
                self.start_table.replace(func)
            elif which.endswith('end'):
                self.dur_table.replace(func)
            elif which.endswith('xfade'):
                self.xfade_table.replace(func)
            elif which.endswith('gain'):
                self.gain_table.replace(func)
            elif which.endswith('trans'):
                self.pitch_table.replace(func)

    def checkForAutomation(self):
        if self.mode != 1:
            if self.start_rec:
                self.start_record.write()
            if self.dur_rec:
                self.dur_record.write()
            if self.xfade_rec:
                self.xfade_record.write()
            if self.gain_rec:
                self.gain_record.write()
            if self.pitch_rec:
                self.pitch_record.write()

    def sig(self):
        return self.mix

    def getDur(self):
        if self.mode != 1:
            return self.table.getDur(False)
        else:
            return _cfg("totalTime")

    def setSound(self, snd):
        if self.mode == 0:
            self.table.setSound(snd)

    def setStart(self, x):
        if self.mode != 1:
            if not self.start_play:
                self.start.value = x

    def setDur(self, x):
        if self.mode != 1:
            if not self.dur_play:
                self.dur.value = x

    def setXfade(self, x):
        if self.mode != 1:
            if not self.xfade_play:
                self.xfade.value = x

    def setGain(self, x):
        if self.mode != 1:
            if not self.gain_play:
                self.gain_in.value = x

    def setPitch(self, x):
        if self.mode != 1:
            if not self.pitch_play:
                self.pitch_in.value = x

    def setLoopMode(self, x):
        if self.mode != 1:
            self.looper.mode = x
            if self.mode == 3:
                self.looper2.mode = x

    def setXfadeShape(self, x):
        if self.mode != 1:
            self.looper.xfadeshape = x
            if self.mode == 3:
                self.looper2.xfadeshape = x

class CeciliaSlider:
    def __init__(self, dic, baseModule):
        self.type = "slider"
        self.name = dic["name"]
        gliss = dic["gliss"]
        up = dic["up"]
        totalTime = _cfg("totalTime")
        self.baseModule = baseModule
        self.widget = w = _snapshot["sliders"][self.name]

        if not up:
            line = _line(self.name)
            self.play = w["play"]
            self.rec = w["rec"]
            self.midi = w["midi"]
            self.openSndCtrl = w["osc"]

            curved = line["curved"]
            log = w["log"]
            if curved and log:
                self.table = CosLogTable()
            elif curved:
                self.table = CosTable()
            elif log:
                self.table = LogTable()
            else:
                self.table = LinTable()
        else:
            self.play = self.rec = self.midi = self.openSndCtrl = 0

        init = w["value"]
        mini = w["min"]
        maxi = w["max"]
        log = w["log"]
        self.slider = SigTo(init, time=gliss, init=init)
        if self.rec:
            self.record = ControlRec(self.slider, filename=w["path"], rate=1000, dur=totalTime).play()
        if self.play > 0:
            self.setGraph(_lineData(line))
            self.reader = TableRead(self.table, freq=1.0 / totalTime).play()
        elif self.midi:
            if log:
                init = math.sqrt((init - mini) / (maxi - mini)) * (maxi - mini) + mini
                exp = 2
            else:
                exp = 1
            self.ctlin = Midictl(w["midictl"], mini, maxi, init, w["midichnl"])
            self.reader = Scale(SigTo(self.ctlin), inmin=mini, inmax=maxi, outmin=mini, outmax=maxi, exp=exp)
        elif self.openSndCtrl:
            port, address = w["openSndCtrl"]
            self.baseModule._addOpenSndCtrlWidget(port, address, self)

    def sig(self):
        if self.play == 0:
            return self.slider
        else:
            return self.reader

    def setValue(self, x):
        self.slider.value = x

    def setGraph(self, func):
        totalTime = _cfg("totalTime")
        func2 = [(int(x / totalTime * 8192), y) for x, y in func[:]]

        oldX = -1
        for i, f in enumerate(func2):
            if f[0] == oldX:
                func2[i] = (f[0]+1, f[1])
            oldX = func2[i][0]

        self.table.replace(func2)

    def updateWidget(self):
        _emit(("slider", self.name), self.reader.get())

    def setValueFromOSC(self, val):
        w = self.widget
        val = rescale(val, ymin=w["min"], ymax=w["max"], ylog=w["log"])
        _emit(("slider", self.name), val)

class CeciliaRange:
    def __init__(self, dic, baseModule):
        self.type = "range"
        self.name = dic["name"]
        gliss = dic["gliss"]
        up = dic["up"]
        totalTime = _cfg("totalTime")
        self.baseModule = baseModule
        self.oscTmpVals = [0, 0]
        self.widget = w = _snapshot["sliders"][self.name]

        if not up:
            self.graph_lines = [_line(self.name, "min"), _line(self.name, "max")]
            self.play = w["play"]
            self.rec = w["rec"]
            self.midi = w["midi"]
            self.openSndCtrl = w["osc"]
            curved = [line["curved"] for line in self.graph_lines]
            if curved[0]:
                self.table_min = CosTable()
            else:
                self.table_min = LinTable()
            if curved[1]:
                self.table_max = CosTable()
            else:
                self.table_max = LinTable()
        else:
            self.play = self.rec = self.midi = self.openSndCtrl = 0

        init = w["value"]
        mini = w["min"]
        maxi = w["max"]
        log = w["log"]
        self.slider = SigTo(init, time=gliss, init=init)
        if self.rec:
            self.record = ControlRec(self.slider, filename=w["path"], rate=1000, dur=totalTime).play()
        if self.play > 0:
            self.setGraph(0, _lineData(self.graph_lines[0]))
            self.reader_min = TableRead(self.table_min, freq=1.0 / totalTime).play()
            self.setGraph(1, _lineData(self.graph_lines[1]))
            self.reader_max = TableRead(self.table_max, freq=1.0 / totalTime).play()
            self.reader = Mix([self.reader_min, self.reader_max], voices=2)
        elif self.midi:
            if log:
                init = [math.sqrt((x - mini) / (maxi - mini)) * (maxi - mini) + mini for x in init]
                exp = 2
            else:
                exp = 1
            self.ctlin = Midictl(w["midictl"], mini, maxi, init, w["midichnl"])
            self.reader = Scale(SigTo(self.ctlin), inmin=mini, inmax=maxi, outmin=mini, outmax=maxi, exp=exp)
        elif self.openSndCtrl:
            oscTuples = w["openSndCtrl"]
            if oscTuples[0] != ():
                port, address = oscTuples[0][0], oscTuples[0][1]
                self.baseModule._addOpenSndCtrlWidget(port, address, self, 0)
            if oscTuples[1] != ():
                port, address = oscTuples[1][0], oscTuples[1][1]
                self.baseModule._addOpenSndCtrlWidget(port, address, self, 1)

    def sig(self):
        if self.play == 0:
            return self.slider
        else:
            return self.reader

    def setValue(self, x):
        self.slider.value = x

    def setGraph(self, which, func):
        totalTime = _cfg("totalTime")
        func = [(int(x / totalTime * 8192), y) for x, y in func]

        oldX = -1
        for i, f in enumerate(func):
            if f[0] == oldX:
                func[i] = (f[0]+1, f[1])
            oldX = func[i][0]

        if which == 0:
            self.table_min.replace(func)
        else:
            self.table_max.replace(func)

    def updateWidget(self):
        _emit(("slider", self.name), self.reader.get(all=True))

    def setValueFromOSC(self, val, which):
        w = self.widget
        val = rescale(val, ymin=w["min"], ymax=w["max"], ylog=w["log"])
        _emit(("slider_one", self.name, which), val)

class CeciliaSplitter:
    def __init__(self, dic):
        self.type = "splitter"
        self.name = dic["name"]
        gliss = dic["gliss"]
        self.num_knobs = dic["num_knobs"]
        # Splitters can't be automated.
        self.play = self.rec = self.midi = 0
        self.widget = _snapshot["sliders"][self.name]
        init = self.widget["value"]
        self.slider = SigTo(init, time=gliss, init=init)

    def sig(self):
        return self.slider

    def setValue(self, x):
        self.slider.value = x

class CeciliaGraph:
    def __init__(self, dic):
        self.name = dic["name"]
        self.isTable = dic["table"]
        self.size = dic["size"]
        totalTime = _cfg("totalTime")
        line = _line(self.name)

        func = [(int(x / totalTime * (self.size - 1)), y) for x, y in line["data"]]

        oldX = -1
        for i, f in enumerate(func):
            if f[0] == oldX:
                func[i] = (f[0]+1, f[1])
            oldX = func[i][0]

        curved = line["curved"]
        if curved:
            self.table = CosTable(func, size=self.size)
        else:
            self.table = LinTable(func, size=self.size)
        if not self.isTable:
            self.reader = TableRead(self.table, freq=1.0 / totalTime).play()

    def sig(self):
        if self.isTable:
            return self.table
        else:
            return self.reader

    def setValue(self, func):
        totalTime = _cfg("totalTime")
        func = [(int(x / totalTime * (self.size - 1)), y) for x, y in func]

        oldX = -1
        for i, f in enumerate(func):
            if f[0] == oldX:
                func[i] = (f[0]+1, f[1])
            oldX = func[i][0]

        self.table.replace(func)

class BaseModule:
    def __init__(self):
        self._fileins = {}
        self._samplers = {}
        self._sliders = {}
        self._toggles = {}
        self._popups = {}
        self._buttons = {}
        self._gens = {}
        self._graphs = {}
        self._polyphony = None
        self._openSndCtrlDict = {}
        self._openSndCtrlSliderDict = {}
        self._OSCOutList = []

        ###### Public attributes ######
        self.sr = _cfg("sr")
        self.nchnls = _cfg("nchnls")
        self.totalTime = _cfg("totalTime")
        self.server = _engine.server
        self.filepath = os.path.split(_cfg("currentCeciliaFile"))[0]
        self.number_of_voices = 1
        self.polyphony_spread = [1.0]
        self.polyphony_scaling = 1.0
        ###############################

        for widget in _engine.interfaceWidgets:
            if widget['type'] in ["cslider", "crange", "csplitter"]:
                self._addSlider(widget, widget["type"])
            elif widget['type'] == "cgraph":
                self._addGraph(widget)
            elif widget['type'] == "ctoggle":
                if widget['rate'] == "k":
                    self._toggles[widget["name"]] = widget
            elif widget['type'] == "cpopup":
                if widget['rate'] == "k":
                    self._popups[widget["name"]] = widget
            elif widget['type'] == "cbutton":
                self._buttons[widget["name"]] = widget
            elif widget['type'] == "cgen":
                if widget['rate'] == "k":
                    self._gens[widget["name"]] = widget
            elif widget['type'] == "cpoly" and self._polyphony is None:
                self._polyphony = widget

        polyname = "noPolyphonyWidget"
        if self._polyphony is not None:
            polyname = self._polyphony["name"]
        for togPop in _snapshot["togglePopups"]:
            name = togPop["name"]
            if name.startswith(polyname):
                if name.endswith("num"):
                    self.number_of_voices = togPop["value"] + 1
                else:
                    self._definePolyTranspo(togPop["label"])
            if togPop["type"] == "popup":
                setattr(self, name + "_index", togPop["value"])
                setattr(self, name + "_value", togPop["label"])
            elif togPop["type"] in ("toggle", "gen"):
                setattr(self, name + "_value", togPop["value"])

        self._metro = Metro(.06).play(dur=self.totalTime)
        self._updater = TrigFunc(self._metro, self._updateWidgets).play(dur=self.totalTime)

    ###### Public methods ######
    def addFilein(self, name):
        """
        Creates a SndTable object from the name of a cfilein widget.

        A SndTable is a memory filled with the samples of the current audio file.

        Parameters:

        name : string
            The name assigned to a cfilein widget in the Interface declaration.

        """
        self._fileins[name] = CeciliaFilein(self, name)
        return self._fileins[name].sig()

    def addSampler(self, name, pitch=1, amp=1):
        """
        Creates a sampler/looper from the name of a csampler widget.

        A sampler comes with an interface window allowing the control of the looping
        parameters (direction, start point, loop duration, crossfade duration, gain,
        pitch, etc.)

        Parameters:

        pitch : float or PyoObject
            External pitch control added to the value of the transposition slider.
        amp : float or PyoObject
            External amplitude control added to the value of the gain slider.

        """
        self._samplers[name] = CeciliaSampler(self, name, pitch, amp)
        return self._samplers[name].sig()

    def getSamplerDur(self, name):
        return self._samplers[name].getDur()

    def duplicate(self, seq, num):
        """
        Duplicates elements in a sequence according to the `num` parameter.

        This method is useful to creates lists that match the number of channels
        multiplied by the number of voices from a cpoly or a splitter widget.

        Parameters:

        seq : list
            List of values to duplicate.
        num : int
            Number of iteration for each value in the sequence.

        Example:

        freqs = duplicate([100, 200, 300], 4)
        print(freqs)
        [100, 100, 100, 100, 200, 200, 200, 200, 300, 300, 300, 300]

        """
        tmp = [x for x in seq for i in range(num)]
        return tmp

    def setGlobalSeed(self, x):
        """
        Sets the Server's global seed used by objects from the random family.

        """
        _engine.server.globalseed = x
    ############################

    ###### Private methods ######
    def _definePolyTranspo(self, chord):
        if self.number_of_voices <= 1:
            return
        tmp = 0
        for i in range(self.number_of_voices):
            tmp -= {0: 0, 1: 3, 2: 3, 3: 2, 4: 2, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1}.get(i, 1)
        self.polyphony_scaling = pow(10.0, tmp * 0.05)
        if chord == "None":
            self.polyphony_spread = [1.0] * self.number_of_voices
            return
        else:
            self.polyphony_spread = [1.0]
        pool = POLY_CHORDS[chord]
        for i in range(1, self.number_of_voices):
            note = pool[i % len(pool)]
            if i % 2 == 1 or note < 1.0:
                self.polyphony_spread.append(midiToTranspo(note + 60))
            else:
                self.polyphony_spread.append(midiToTranspo(note - 12 + 60))

    def _deleteOscReceivers(self):
        if hasattr(self, "oscReceivers"):
            for key in list(self.oscReceivers.keys()):
                del self.oscReceivers[key]
            del self.oscReceivers

    def _createOpenSndCtrlReceivers(self):
        if self._openSndCtrlDict:
            self.oscReceivers = {}
            for key in self._openSndCtrlDict.keys():
                self.oscReceivers[key] = OscReceive(key, self._openSndCtrlDict[key])
                for slider in self._openSndCtrlSliderDict[key]:
                    if isinstance(slider, tuple):
                        slider, side = slider[0], slider[1]
                        if slider.type == "sampler": # sampler slider
                            widget = slider.getWidget(side)
                            openSndCtrl, OSCOut = widget["openSndCtrl"], widget["OSCOut"]
                            value = widget["value"]
                        else: # range slider
                            widget = slider.widget
                            openSndCtrl = widget["openSndCtrl"][side]
                            OSCOut = widget["OSCOut"][side] if widget["OSCOut"] is not None else None
                            if OSCOut == ():
                                OSCOut = None
                            value = widget["value"][side]
                    else: # slider
                        widget = slider.widget
                        openSndCtrl, OSCOut = widget["openSndCtrl"], widget["OSCOut"]
                        value = widget["value"]
                    path = openSndCtrl[1]
                    val = rescale(value, xmin=widget["min"], xmax=widget["max"], xlog=widget["log"])
                    self.oscReceivers[key].setValue(path, val)
                    if OSCOut is not None:
                        tmpout = OscDataSend("f", OSCOut[1], OSCOut[2], OSCOut[0])
                        tmpout.send([val])
                        self._OSCOutList.append(tmpout)

    def _addOpenSndCtrlWidget(self, port, address, slider, side=0, name=""):
        if port in self._openSndCtrlDict:
            self._openSndCtrlDict[port].append(address)
            if slider.type == 'sampler':
                self._openSndCtrlSliderDict[port].append((slider, name))
            elif slider.type == 'slider':
                self._openSndCtrlSliderDict[port].append(slider)
            elif slider.type == 'range':
                self._openSndCtrlSliderDict[port].append((slider, side))
        else:
            self._openSndCtrlDict[port] = [address]
            if slider.type == 'sampler':
                self._openSndCtrlSliderDict[port] = [(slider, name)]
            elif slider.type == 'slider':
                self._openSndCtrlSliderDict[port] = [slider]
            elif slider.type == 'range':
                self._openSndCtrlSliderDict[port] = [(slider, side)]

    def _checkForAutomation(self):
        for sampler in self._samplers.values():
            sampler.checkForAutomation()
        for slider in self._sliders.values():
            if slider.rec:
                slider.record.write()

    def _updateWidgets(self):
        if self._samplers != {}:
            for key in self._samplers.keys():
                self._samplers[key].updateWidgets()
        for slider in self._sliders.values():
            if slider.play == 1 or slider.midi:
                slider.updateWidget()
        if self._openSndCtrlDict:
            for key in self._openSndCtrlDict.keys():
                values = self.oscReceivers[key].get(all=True)
                for i in range(len(values)):
                    slider = self._openSndCtrlSliderDict[key][i]
                    if isinstance(slider, tuple):
                        which = slider[1]
                        slider[0].setValueFromOSC(values[i], which)
                    else:
                        slider.setValueFromOSC(values[i])
        _engine.updatePluginWidgets()

    def _setWidgetValues(self):
        # graph lines
        for line in _snapshot["lines"]:
            name = line["name"]
            if name in self._graphs.keys():
                self._graphs[name].setValue(line["data"])
        # sliders
        for name in _snapshot["userSliders"]:
            self._sliders[name].setValue(_snapshot["sliders"][name]["value"])
        # toggles and popups
        for togPop in _snapshot["togglePopups"]:
            name = togPop["name"]
            if name in self._toggles:
                getattr(self, name)(togPop["value"])
            elif name in self._popups:
                getattr(self, name)(togPop["value"], togPop["label"])
            elif name in self._gens:
                getattr(self, name)(togPop["value"])

    def _addSlider(self, dic, typ):
        if typ == "cslider":
            self._sliders[dic["name"]] = CeciliaSlider(dic, self)
        elif typ == "crange":
            self._sliders[dic["name"]] = CeciliaRange(dic, self)
        elif typ == "csplitter":
            self._sliders[dic["name"]] = CeciliaSplitter(dic)
        setattr(self, dic["name"], self._sliders[dic["name"]].sig())

    def _addGraph(self, dic):
        self._graphs[dic["name"]] = CeciliaGraph(dic)
        setattr(self, dic["name"], self._graphs[dic["name"]].sig())

    def _cleanup(self):
        self.oscReceivers = {}
        self._OSCOutList = []
        for key in list(self.__dict__.keys()):
            del self.__dict__[key]

class CeciliaPlugin:
    def __init__(self, input, params=None, knobs=None):
        """knobs: snapshot dicts of the plugin's three knobs (see snapshot.py)."""
        self.input = Sig(input)
        gliss = 0.05
        totalTime = _cfg("totalTime")
        if params is None:
            self._p1 = SigTo(0, time=0.025)
            self._p2 = SigTo(0, time=0.025)
            self._p3 = SigTo(0, time=0.025)
        else:
            for i in range(3):
                n = i + 1
                knob = knobs[i]
                setattr(self, "widget_p%d" % n, knob)
                line = _line(knob["name"])
                play, rec, midi = knob["play"], knob["rec"], knob["midi"]
                setattr(self, "play_p%d" % n, play)
                setattr(self, "rec_p%d" % n, rec)
                setattr(self, "midi_p%d" % n, midi)
                table = CosTable() if line["curved"] else LinTable()
                setattr(self, "table_p%d" % n, table)

                init, mini, maxi, log = knob["value"], knob["min"], knob["max"], knob["log"]
                param = SigTo(params[i], time=gliss, init=params[i])
                setattr(self, "_p%d" % n, param)
                if rec:
                    setattr(self, "record_p%d" % n,
                            ControlRec(param, filename=knob["path"], rate=1000, dur=totalTime).play())
                if play > 0:
                    self.setGraph(i, _lineData(line))
                    setattr(self, "reader_p%d" % n, TableRead(table, freq=1.0 / totalTime).play())
                elif midi:
                    if log:
                        init = math.sqrt((init - mini) / (maxi - mini)) * (maxi - mini) + mini
                        exp = 2
                    else:
                        exp = 1
                    ctlin = Midictl(knob["midictl"], mini, maxi, init, knob["midichnl"])
                    setattr(self, "ctlin_p%d" % n, ctlin)
                    setattr(self, "reader_p%d" % n, Scale(ctlin, inmin=mini, inmax=maxi, outmin=mini, outmax=maxi, exp=exp))

            self.preset = params[3]

    def sig1(self):
        if self.play_p1 == 0 and not self.midi_p1:
            return self._p1
        else:
            return self.reader_p1

    def sig2(self):
        if self.play_p2 == 0 and not self.midi_p2:
            return self._p2
        else:
            return self.reader_p2

    def sig3(self):
        if self.play_p3 == 0 and not self.midi_p3:
            return self._p3
        else:
            return self.reader_p3

    def setPreset(self, x, label):
        self.preset = x
        if self.preset == 0:
            self.out.interp = 0
        else:
            self.out.interp = 1

    def setInput(self, input):
        self.input.value = input

    def setGraph(self, which, func):
        totalTime = _cfg("totalTime")
        func = [(int(x / totalTime * 8192), y) for x, y in func]
        if which == 0:
            self.table_p1.replace(func)
        elif which == 1:
            self.table_p2.replace(func)
        elif which == 2:
            self.table_p3.replace(func)

    def stopControls(self):
        self._p1.stop()
        self._p2.stop()
        self._p3.stop()

    def startControls(self):
        self._p1.play()
        self._p2.play()
        self._p3.play()

    def setValue(self, which, x):
        if which == 0:
            self._p1.value = x
        elif which == 1:
            self._p2.value = x
        elif which == 2:
            self._p3.value = x

    def checkForAutomation(self):
        if self.rec_p1:
            self.record_p1.write()
        if self.rec_p2:
            self.record_p2.write()
        if self.rec_p3:
            self.record_p3.write()

    def updateWidget(self, order):
        """order: the plugin's current slot, which is also its slot in the interface."""
        if self.play_p1 or self.midi_p1:
            _emit(("plugin_knob", order, 0), self.name, self.reader_p1.get())
        if self.play_p2 or self.midi_p2:
            _emit(("plugin_knob", order, 1), self.name, self.reader_p2.get())
        if self.play_p3 or self.midi_p3:
            _emit(("plugin_knob", order, 2), self.name, self.reader_p3.get())

class CeciliaNonePlugin(CeciliaPlugin):
    def __init__(self, input):
        CeciliaPlugin.__init__(self, input)
        self.out = self.input
        self.stopControls()

class CeciliaReverbPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        self.out = Freeverb(self.input, size=self.sig2() * 0.1, damp=self.sig3(), bal=self.sig1() * self.preset)

    def setPreset(self, x, label):
        self.preset = x
        self.out.bal = self.sig1() * self.preset

class CeciliaWGReverbPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        self.out = WGVerb(self.input, feedback=self.sig2(), cutoff=self.sig3(), bal=self.sig1() * self.preset)

    def setPreset(self, x, label):
        self.preset = x
        self.out.bal = self.sig1() * self.preset

class CeciliaFilterPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
            typ = 0
        else:
            inter = 1
            typ = self.preset - 1
        self.filter = Biquad(self.input, freq=self.sig2(), q=self.sig3(), type=typ, mul=self.sig1())
        self.out = Interp(self.input, self.filter, inter)

    def setPreset(self, x, label):
        self.preset = x
        if self.preset == 0:
            self.out.interp = 0
        else:
            self.filter.type = self.preset - 1
            self.out.interp = 1

class CeciliaEQPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
            typ = 0
        else:
            inter = 1
            typ = self.preset - 1
        self.filter = EQ(self.input, freq=self.sig1(), q=self.sig2(), boost=self.sig3(), type=typ)
        self.out = Interp(self.input, self.filter, inter)

    def setPreset(self, x, label):
        self.preset = x
        if self.preset == 0:
            self.out.interp = 0
        else:
            self.filter.type = self.preset - 1
            self.out.interp = 1

class CeciliaChorusPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        self.out = Chorus(self.input, depth=self.sig2(), feedback=self.sig3(), bal=self.sig1() * self.preset)

    def setPreset(self, x, label):
        self.preset = x
        self.out.bal = self.sig1() * self.preset

class CeciliaEQ3BPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.low = EQ(self.input, freq=250, q=0.707, boost=self.sig1(), type=1)
        self.mid = EQ(self.low, freq=1500, q=0.707, boost=self.sig2(), type=0)
        self.high = EQ(self.mid, freq=2500, q=0.707, boost=self.sig3(), type=2)
        self.out = Interp(self.input, self.high, inter)

class CeciliaCompressPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.dbtoamp = DBToA(self.sig3())
        self.comp = Compress(self.input, thresh=self.sig1(), ratio=self.sig2(), lookahead=4, knee=.5, mul=self.dbtoamp)
        self.out = Interp(self.input, self.comp, inter)

class CeciliaGatePlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.gate = Gate(self.input, thresh=self.sig1(), risetime=self.sig2(), falltime=self.sig3(), lookahead=4)
        self.out = Interp(self.input, self.gate, inter)

class CeciliaDistoPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.gain = DBToA(self.sig3())
        self.disto = Disto(self.input, drive=self.sig1(), slope=self.sig2(), mul=self.gain)
        self.out = Interp(self.input, self.disto, inter)

class CeciliaAmpModPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            mode = self.preset - 1
            inter = 1

        self.zero = Sig(0)
        if len(self.input) < 2:
            self.lfoamp = Sine(freq=self.sig1(), mul=.5, add=.5)
            self.iamp = 1.0 - self.sig2()
            self.modamp = self.input * (self.lfoamp * self.sig2() + self.iamp)
            self.lforing = Sine(freq=self.sig1(), mul=self.sig2())
            self.modring = self.input * self.lforing
        else:
            self.lfoamp = Sine(freq=self.sig1(), phase=[self.zero, self.sig3()], mul=.5, add=.5)
            self.iamp = 1.0 - self.sig2()
            self.modamp = self.input * (self.lfoamp * self.sig2() + self.iamp)
            self.lforing = Sine(freq=self.sig1(), phase=[self.zero, self.sig3()], mul=self.sig2())
            self.modring = self.input * self.lforing

        if mode == 0:
            self.out = Interp(self.input, self.modamp, inter)
        else:
            self.out = Interp(self.input, self.modring, inter)

    def setPreset(self, x, label):
        self.preset = x
        if self.preset == 0:
            self.out.interp = 0
        else:
            if self.preset == 1:
                self.out.input2 = self.modamp
            else:
                self.out.input2 = self.modring
            self.out.interp = 1

class CeciliaPhaserPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.phaser = Phaser(self.input, freq=self.sig1(), spread=self.sig3(), q=self.sig2(), feedback=0.8, num=8, mul=.5)
        self.out = Interp(self.input, self.phaser, inter)

class CeciliaDelayPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.gain = DBToA(self.sig3())
        self.delaytime = SigTo(self.sig1(), time=.1, init=.1)
        self.delay = Delay(self.input, delay=self.delaytime, feedback=self.sig2())
        self.delaymix = Interp(self.input, self.delay, self.sig3())
        self.out = Interp(self.input, self.delaymix, inter)

class CeciliaFlangePlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.lfo = Sine(freq=self.sig2(), mul=self.sig1() * 0.005, add=0.005)
        self.delay = Delay(self.input, delay=self.lfo, feedback=self.sig3())
        self.delaymix = self.delay + self.input
        self.out = Interp(self.input, self.delaymix, inter)

class CeciliaHarmonizerPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.harmo = Harmonizer(self.input, transpo=self.sig1(), feedback=self.sig2())
        self.mix = Interp(self.input, self.harmo, self.sig3())
        self.out = Interp(self.input, self.mix, inter)

class CeciliaResonatorsPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.wg1 = Waveguide(self.input, freq=self.sig1(), dur=30, mul=.05)
        self.f2 = self.sig1() * self.sig2()
        self.wg2 = Waveguide(self.input, freq=self.f2, dur=30, mul=.05)
        self.f3 = self.sig1() * self.sig2() * self.sig2()
        self.wg3 = Waveguide(self.input, freq=self.f3, dur=30, mul=.05)
        self.f4 = self.sig1() * self.sig2() * self.sig2() * self.sig2()
        self.wg4 = Waveguide(self.input, freq=self.f4, dur=30, mul=.05)
        self.total = self.wg1 + self.wg2 + self.wg3 + self.wg4
        self.mix = Interp(self.input, self.total, self.sig3())
        self.out = Interp(self.input, self.mix, inter)

class CeciliaDeadResonPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)
        if self.preset == 0:
            inter = 0
        else:
            inter = 1
        self.wg1 = AllpassWG(self.input, freq=self.sig1(), feed=.995, detune=self.sig2(), mul=.1)
        self.wg2 = AllpassWG(self.input, freq=self.sig1() * 0.993, feed=.995, detune=self.sig2(), mul=.1)
        self.total = self.wg1 + self.wg2
        self.mix = Interp(self.input, self.total, self.sig3())
        self.out = Interp(self.input, self.mix, inter)

class CeciliaChaosModPlugin(CeciliaPlugin):
    def __init__(self, input, params, knobs):
        CeciliaPlugin.__init__(self, input, params, knobs)

        self.lfolo = Lorenz(self.sig1(), self.sig2(), stereo=True, mul=0.5, add=0.5).stop()
        self.lforo = Rossler(self.sig1(), self.sig2(), stereo=True, mul=0.5, add=0.5).stop()
        self.lfoch = ChenLee(self.sig1(), self.sig2(), stereo=True, mul=0.5, add=0.5).stop()
        self.lfo = Sig(self.lfolo)
        self.iamp = 1.0 - self.sig3()
        self.modu = self.input * (self.lfo * self.sig3() + self.iamp)

        if self.preset == 0:
            inter = 0
        else:
            if self.preset == 1:
                self.lfo.value = self.lfolo.play()
            elif self.preset == 2:
                self.lfo.value = self.lfoch.play()
            else:
                self.lfo.value = self.lforo.play()
            inter = 1

        self.out = Interp(self.input, self.modu, inter)

    def setPreset(self, x, label):
        self.preset = x
        if self.preset == 0:
            self.out.interp = 0
        else:
            self.lfo.value.stop()
            if self.preset == 1:
                self.lfo.value = self.lfolo.play()
            elif self.preset == 2:
                self.lfo.value = self.lfoch.play()
            else:
                self.lfo.value = self.lforo.play()
            self.out.interp = 1

class _MeterForwarder:
    """pyo's amplitude callback object. pyo calls setRms() from the audio
    thread. Levels are sent to the interface at most about 30 times per
    second, with the loudest level seen since the previous send (so short
    peaks aren't lost)."""
    INTERVAL = 0.03

    def __init__(self):
        self.peak = 0.0
        self.last = 0.0

    def setRms(self, *args):
        if args and args[0] >= 0:
            loudest = max(args)
            if loudest > self.peak:
                self.peak = loudest
        now = time.monotonic()
        if now - self.last >= self.INTERVAL:
            self.last = now
            peak, self.peak = self.peak, 0.0
            _emit(("meter",), args, peak)


def formatModuleError(filepath, msg):
    """Error message for a module that failed to compile or run, from the
    exception being handled."""
    error = traceback.format_exc()
    if "exec(f.read(), globals())" in error:
        pos = error.find("exec(f.read(), globals())")
        error = error[pos:].replace("exec(f.read(), globals())", "")

    linenum = -1
    if "line " in error:
        try:
            pos1 = error.rfind("line ") + 5
            pos2 = pos1
            while error[pos2] in "0123456789":
                pos2 += 1
            linenum = int(error[pos1:pos2].strip())
        except:
            pass

    codeline = ""
    if linenum != -1:
        try:
            codeline = "Please review this part of the code and reload the module:\n\n#-----\n"
            with open(filepath, "r") as f:
                _text = f.readlines()
                if linenum > 0:
                    codeline = codeline + _text[linenum-1] + _text[linenum] + "#-----\n"
                else:
                    codeline = codeline + _text[linenum] + "#-----\n"
        except:
            pass

    tracelines = "Traceback:\n\n" + error.replace("<string>", filepath) + "\n\n"
    return msg + tracelines + codeline


class AudioServer():
    """The engine: owns the pyo Server, the module being played and the
    plugin chain.

    cfg: engine preferences (see snapshot.enginePrefs()), updated with
         configure() and by each snapshot given to play().
    sink: where engine events go (see the top of this file).

    The public methods listed in COMMANDS are the whole interface of the
    engine; nothing else is called from the GUI.
    """
    COMMANDS = ("configure", "updateDebug", "openModule", "play", "stop", "writeAutomation",
                "isRunning", "isBooted", "setAmp", "setSlider", "callModule", "samplerCall",
                "setGraph", "setSliderGraph", "setSamplerGraph", "setPluginGraph",
                "setPlugin", "movePlugin", "setPluginValue", "setPluginPreset",
                "midiLearn", "spectrumCall", "shutdown", "testSeed")

    def __init__(self, sink, cfg):
        global _engine
        _engine = self
        self.sink = sink
        self.cfg = dict(cfg)
        self.amp = 1.0
        self.module = None          # the running BaseModule instance
        self.moduleClass = None     # Module class of the opened .c5 file
        self.interfaceWidgets = []  # Interface of the opened .c5 file
        sr, bufsize, nchnls, duplex, host, outdev, indev, firstin, firstout = self.getPrefs()
        if self.cfg["DEBUG"]:
            print("AUDIO CONFIG:")
            template = "sr: %s, buffer size: %s, num of channels: %s, duplex: %s, host: %s, output device: %s, input device: %s"
            print(template % (sr, bufsize, nchnls, duplex, host, outdev, indev))
            print("first physical input: %s, first physical output: %s\n" % (firstin, firstout))
        self.server = Server(sr=sr, buffersize=bufsize, nchnls=nchnls, duplex=duplex,
                             audio=host, jackname=self.cfg["jackClient"])
        if self.cfg["DEBUG"]:
            self.server.verbosity = 15
        if host == 'jack':
            self.server.setJackAuto(True, True)
        self.server._server.setTimeCallable(self)
        self.server._server.setAmpCallable(_MeterForwarder())
        self.timeOpened = True
        self.endDialogPosted = self.endStopPosted = False
        self.recording = False
        self.withTimer = False
        self.withSpectrum = False
        self.pluginObjs = [None] * NUM_OF_PLUGINS
        self.out = self.spectrum = None
        self.seed = None             # see testSeed()
        # In the engine process, the end of an offline render is reported
        # when the server has actually stopped (see engine_process.py).
        self.deferOfflineEnd = False
        self.pluginDict = {"Reverb": CeciliaReverbPlugin, "WGVerb": CeciliaWGReverbPlugin, "Filter": CeciliaFilterPlugin, "Para EQ": CeciliaEQPlugin,
                           "Chorus": CeciliaChorusPlugin, "3 Bands EQ": CeciliaEQ3BPlugin, "Compress": CeciliaCompressPlugin, "Gate": CeciliaGatePlugin,
                           "Disto": CeciliaDistoPlugin, "AmpMod": CeciliaAmpModPlugin, "Phaser": CeciliaPhaserPlugin, "Delay": CeciliaDelayPlugin,
                           "Flange": CeciliaFlangePlugin, "Harmonizer": CeciliaHarmonizerPlugin, "Resonators": CeciliaResonatorsPlugin,
                           "DeadReson": CeciliaDeadResonPlugin, 'ChaosMod': CeciliaChaosModPlugin}

    ###### Commands ######
    def configure(self, cfg):
        self.cfg.update(cfg)

    def updateDebug(self, debug):
        self.cfg["DEBUG"] = debug
        if debug:
            self.server.verbosity = 15
        else:
            self.server.verbosity = 7

    def openModule(self, filepath):
        """Compiles a .c5 file. Returns (True, interface widgets) or
        (False, error message)."""
        global Module, Interface

        try:
            del Module, Interface
        except:
            pass

        if not serverBooted():
            self.boot()

        self.cleanup_module()
        self.moduleClass = None
        self.interfaceWidgets = []

        try:
            with open(filepath, "r") as f:
                exec(f.read(), globals())
        except Exception:
            msg = "Cecilia can't compile the chosen module, Current module (or a random one if this failed too) will be reloaded.\n\n"
            return False, formatModuleError(filepath, msg)

        self.moduleClass = Module
        self.interfaceWidgets = deepCopy(Interface)
        return True, deepCopy(Interface)

    def play(self, snapshot, timer=True, rec=False):
        """Boots the server with the snapshot's preferences, builds the module
        and starts it. Returns (True, None) or (False, error message)."""
        self.configure(snapshot["prefs"])
        self.shutdown()
        self.reinit()
        if self.seed is not None:
            self.server.setGlobalSeed(self.seed)
            random.seed(self.seed)
        self.boot()
        if self.moduleClass is None:
            return False, None
        ok, error = self.loadModule(snapshot)
        if not ok:
            return False, error
        self.start(timer=timer, rec=rec)
        return True, None

    def stop(self):
        if not self.isRunning():
            return
        if self.cfg["DEBUG"]:
            print("Audio server stop: begin")
        if self.withTimer:
            self.withTimer = False
        self.server.stop()
        if self.recording:
            self.recording = False
            self.recorder.stop()
        if self.withSpectrum:
            self.withSpectrum = False
            self.spectrum.poll(False)
            self.spectrum.stop()
        self.timeOpened = False
        self.sink.clear()
        _emit(("clock",), "cursor")  # back to the start offset
        time.sleep(.15)
        if self.module is not None:
            self.module._deleteOscReceivers()
        if self.cfg["DEBUG"]:
            print("Audio server stop: end\n")

    def writeAutomation(self):
        """Writes the automation recorded during the last run to disk."""
        if self.module is not None:
            self.checkForAutomation()
            self.module._checkForAutomation()

    def isRunning(self):
        return self.server.getIsStarted()

    def isBooted(self):
        return self.server.getIsBooted()

    def setAmp(self, x):
        self.amp = math.pow(10.0, x * 0.05)
        self.server.amp = self.amp
        try:
            self.recamp.value = self.amp
        except:
            pass
        try:
            self.specamp.value = self.amp
        except:
            pass

    def setSlider(self, name, value):
        if self.module is not None and name in self.module._sliders:
            self.module._sliders[name].setValue(value)

    def callModule(self, name, *args):
        """Calls a method of the module: toggles, popups, buttons, gens and
        the `<name>_up` callbacks of sliders."""
        if self.module is not None:
            getattr(self.module, name)(*args)

    def samplerCall(self, name, method, *args):
        """Calls a method of a module's sampler (setStart, setDur, setXfade,
        setGain, setPitch, setLoopMode, setXfadeShape, setSound)."""
        if self.module is not None and name in self.module._samplers:
            getattr(self.module._samplers[name], method)(*args)

    def setGraph(self, name, data):
        if self.module is not None and name in self.module._graphs:
            self.module._graphs[name].setValue(data)

    def setSliderGraph(self, name, data, which=None):
        if self.module is not None and name in self.module._sliders:
            if which is None:
                self.module._sliders[name].setGraph(data)
            else:
                self.module._sliders[name].setGraph(which, data)

    def setSamplerGraph(self, name, line, data):
        self.samplerCall(name, "setGraph", line, data)

    def setPluginGraph(self, order, name, which, func):
        if self._pluginIs(order, name):
            self.pluginObjs[order].setGraph(which, func)

    def setPlugin(self, order, plugin):
        """Replaces the plugin in slot `order` while playing. plugin is a
        snapshot dict (see snapshot.py) or None."""
        if not self.isRunning():
            return
        tmp = self.pluginObjs[order]
        if order == 0:
            tmp_out = self.out
        else:
            tmp_out = self.pluginObjs[order - 1].out
        self.pluginObjs[order] = self._makePlugin(tmp_out, plugin)
        if order < (NUM_OF_PLUGINS - 1):
            self.pluginObjs[order + 1].setInput(self.pluginObjs[order].out)
        else:
            self.pluginObjs[order].out.out()
        del tmp

    def movePlugin(self, vpos, dir):
        if not self.isRunning():
            return
        i1 = vpos
        i2 = vpos + dir
        tmp = self.pluginObjs[i2]
        self.pluginObjs[i2] = self.pluginObjs[i1]
        self.pluginObjs[i1] = tmp
        for i in range(NUM_OF_PLUGINS):
            if i == 0:
                tmp_out = self.out
            else:
                tmp_out = self.pluginObjs[i - 1].out
            self.pluginObjs[i].setInput(tmp_out)
            self.pluginObjs[i].out.play()
        self.pluginObjs[NUM_OF_PLUGINS - 1].out.out()

    def setPluginValue(self, order, name, which, x):
        if self._pluginIs(order, name):
            self.pluginObjs[order].setValue(which, x)

    def setPluginPreset(self, order, name, which, label):
        if self._pluginIs(order, name):
            self.pluginObjs[order].setPreset(which, label)

    def midiLearn(self):
        """Restarts the server listening to MIDI controllers. Each controller
        move is sent as a ("midi_ctl", number, channel) event; the interface
        calls stop() when it has learned what it wanted."""
        self.shutdown()
        self.boot()
        self.scan = CtlScan2(self._onMidiCtl, False)
        self.server.start()

    def spectrumCall(self, method, *args):
        """Forwards a setting of the spectrum window to the analyzer."""
        if self.spectrum is None:
            return
        if method in ("wintype", "size"):
            setattr(self.spectrum, method, args[0])
        else:
            getattr(self.spectrum, method)(*args)

    def shutdown(self):
        self.server.shutdown()

    def testSeed(self, seed):
        """Test tools only: seeds pyo and Python's random module before each
        play, so renders are repeatable."""
        self.seed = seed

    ###### Internals ######
    def getPrefs(self):
        c = self.cfg
        return (c["sr"], int(c["bufferSize"]), c["nchnls"], c["enableAudioInput"], c["audioHostAPI"],
                c["audioOutput"], c["audioInput"], c["defaultFirstInput"], c["defaultFirstOutput"])

    def start(self, timer=True, rec=False):
        if self.cfg["DEBUG"]:
            print("Audio server start: begin")
        self.timeOpened = True
        self.endDialogPosted = self.endStopPosted = False
        fade = self.cfg["globalFade"]
        self.globalamp = Fader(fadein=fade, fadeout=fade, dur=self.cfg["totalTime"]).play()
        self.out.mul = self.globalamp
        if self.cfg["automaticMidiBinding"] and self.cfg["useMidi"]:
            if self.cfg["DEBUG"]:
                print("Audio server start: use midi")
                print("midi input device: %d" % self.cfg["midiDeviceIn"])
            self.checkCtl7 = Midictl(ctlnumber=7, minscale=-48, maxscale=18, init=0)
            self.onNewCtl7Value = Change(self.checkCtl7)
            self.ctl7TrigFunc = TrigFunc(self.onNewCtl7Value, self.newCtl7Value)
        if rec:
            self.recording = True
            self.recamp = SigTo(self.amp, time=0.05, init=self.amp)
            self.recorder = Record(self.pluginObjs[-1].out * self.recamp,
                                   self.cfg["outputFile"], self.cfg["nchnls"],
                                   fileformat=self.cfg["fileFormat"], sampletype=self.cfg["sampleType"],
                                   buffering=8)
        if self.cfg["showSpectrum"]:
            self.withSpectrum = True
            self.specamp = SigTo(self.amp, time=0.05, init=self.amp, mul=self.pluginObjs[-1].out)
            self.spectrum = Spectrum(self.specamp, function=self._spectrumPoints)
        if self.cfg["startOffset"] > 0.0:
            self.server.startoffset = self.cfg["startOffset"]
        self.withTimer = timer
        self.server.start()
        if self.cfg["DEBUG"]:
            print("Audio server start: end\n")

    def boot(self):
        sr, bufsize, nchnls, duplex, host, outdev, indev, firstin, firstout = self.getPrefs()
        if self.cfg["DEBUG"]:
            print("AUDIO CONFIG:")
            template = "sr: %s, buffer size: %s, num of channels: %s, duplex: %s, host: %s, output device: %s, input device: %s"
            print(template % (sr, bufsize, nchnls, duplex, host, outdev, indev))
            print("first physical input: %s, first physical output: %s\n" % (firstin, firstout))
            print("MIDI CONFIG: \ninput device: %d\n" % self.cfg["midiDeviceIn"])
        self.server.setSamplingRate(sr)
        self.server.setBufferSize(bufsize)
        self.server.setNchnls(nchnls)
        self.server.setDuplex(duplex)
        self.server.setOutputDevice(outdev)
        self.server.setInputOffset(firstin)
        self.server.setOutputOffset(firstout)
        if self.cfg["enableAudioInput"]:
            self.server.setInputDevice(indev)
        if self.cfg["useMidi"]:
            self.server.setMidiInputDevice(self.cfg["midiDeviceIn"])
        self.server.boot()

    def reinit(self):
        jackname = self.cfg["jackClient"]
        if self.cfg["toDac"]:
            host = self.cfg["audioHostAPI"]
            self.server.reinit(audio=host, jackname=jackname)
        else:
            self.server.reinit(audio="offline_nb", jackname=jackname)
            self.server.recordOptions(dur=self.cfg["totalTime"], filename=self.cfg["outputFile"],
                                      fileformat=self.cfg["fileFormat"], sampletype=self.cfg["sampleType"])

    def setTime(self, *args):
        # Called from the audio thread (or pyo's offline render thread) every
        # few milliseconds of audio. Only sends events.
        if len(args) >= 4 and self.timeOpened:
            time = args[1] * 60 + args[2] + args[3] * 0.001
            # (offline, the clock shows the progress of the render)
            _emit(("clock",), (time, args[1], args[2], args[3] // 10))
            totalTime = self.cfg["totalTime"]
            if time >= (totalTime - 0.5) and not self.endDialogPosted:
                self.endDialogPosted = True
                _event("near_end")
            if self.deferOfflineEnd and not self.cfg["toDac"]:
                pass
            elif time >= totalTime and not self.endStopPosted:
                self.endStopPosted = True
                _event("end")
        else:
            _emit(("clock",), "reset")

    def newCtl7Value(self):
        # audio thread
        _emit(("gain_ctl7",), self.checkCtl7.get())

    def _onMidiCtl(self, number, midichnl=1):
        # audio thread (CtlScan2 callback)
        _event("midi_ctl", number, midichnl)

    def _spectrumPoints(self, points):
        # audio thread (Spectrum's refresh timer)
        _emit(("spectrum",), points)

    def _pluginIs(self, order, name):
        obj = self.pluginObjs[order]
        return obj is not None and obj.name == name and name != "None"

    def _makePlugin(self, input, plugin):
        if plugin is None:
            obj = CeciliaNonePlugin(input)
            obj.name = "None"
        else:
            obj = self.pluginDict[plugin["name"]](input, plugin["params"], plugin["knobs"])
            obj.name = plugin["name"]
        return obj

    def cleanup_module(self):
        for i in range(NUM_OF_PLUGINS):
            if self.pluginObjs[i] is not None:
               del self.pluginObjs[i].out
               self.pluginObjs[i] = None
        if self.spectrum is not None:
            self.specamp.mul = 1
            self.spectrum.setFunction(None)
            del self.specamp
            del self.spectrum._timer
            del self.spectrum
            self.spectrum = None
        if hasattr(self, "out"):
            if self.out is not None:
                del self.out
                self.out = None
        try:
            del self.globalamp
        except:
            pass
        try:
            del self.endcall
        except:
            pass
        try:
            del self.recorder
            del self.recamp
        except:
            pass
        try:
            del self.checkCtl7
            del self.onNewCtl7Value
            del self.ctl7TrigFunc
        except:
            pass
        if self.module is not None:
            try:
                self.module._cleanup()
            except:
                pass
            self.module = None

    def loadModule(self, snapshot):
        global _snapshot
        self.cleanup_module()
        _snapshot = snapshot

        try:
            currentModule = self.moduleClass()
        except Exception:
            msg = "Cecilia can't run the current module, last valid module (or a random one if this failed too) will be reloaded.\n\n"
            return False, formatModuleError(self.cfg["currentCeciliaFile"], msg)

        currentModule._createOpenSndCtrlReceivers()
        self.out = Sig(currentModule.out)

        for i in range(NUM_OF_PLUGINS):
            if i == 0:
                tmp_out = self.out
            else:
                tmp_out = self.pluginObjs[i - 1].out
            self.pluginObjs[i] = self._makePlugin(tmp_out, snapshot["plugins"][i])

        self.pluginObjs[NUM_OF_PLUGINS - 1].out.out()
        self.module = currentModule
        currentModule._setWidgetValues()

        return True, None

    def checkForAutomation(self):
        for i in range(NUM_OF_PLUGINS):
            if self.pluginObjs[i] is not None and self.pluginObjs[i].name != "None":
                self.pluginObjs[i].checkForAutomation()

    def updatePluginWidgets(self):
        for i in range(NUM_OF_PLUGINS):
            if self.pluginObjs[i] is not None and self.pluginObjs[i].name != "None":
                self.pluginObjs[i].updateWidget(i)
