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
Sound file and device information, read with pyo's helper functions in the
interface's process (no audio engine involved).
"""
import os, math
import Resources.CeciliaLib as CeciliaLib
from pyo import (sndinfo, pa_get_input_devices, pa_get_default_input, pa_get_output_devices,
                 pa_get_default_output, pm_get_input_devices, pm_get_default_input)


def getAvailableAudioMidiDrivers():
    inputDriverList, inputDriverIndexes = pa_get_input_devices()
    try:
        defaultInputDriver = inputDriverList[inputDriverIndexes.index(pa_get_default_input())]
    except:
        defaultInputDriver = ""
    outputDriverList, outputDriverIndexes = pa_get_output_devices()
    try:
        defaultOutputDriver = outputDriverList[outputDriverIndexes.index(pa_get_default_output())]
    except:
        defaultOutputDriver = ""
    midiDriverList, midiDriverIndexes = pm_get_input_devices()
    try:
        defaultMidiDriver = midiDriverList[midiDriverIndexes.index(pm_get_default_input())]
    except:
        defaultMidiDriver = ""
    if len(midiDriverList) > 1:
        midiDriverList.append("all")
        midiDriverIndexes.append(99)
    return inputDriverList, inputDriverIndexes, defaultInputDriver, outputDriverList, outputDriverIndexes, \
            defaultOutputDriver, midiDriverList, midiDriverIndexes, defaultMidiDriver


def validateAudioFile(path):
    return sndinfo(path) is not None


def getSoundInfo(path):
    """
    Retrieves information of the sound and prints it to the console.

    return (number of channels, sampling rate, duration, fraction of a table, length in samples, bitrate)
    """
    if CeciliaLib.getVar("DEBUG"):
        print('--------------------------------------')
        print(path)

    info = sndinfo(path)

    if info is not None:
        samprate = info[2]
        chnls = info[3]
        nsamps = info[0]
        dur = info[1]
        bitrate = info[5]
        format = info[4]
        for i in range(24):
            size = math.pow(2, (i + 1))
            if size > nsamps:
                break
        tableFrac = nsamps / size

        if CeciliaLib.getVar("DEBUG"):
            print("channels = %d" % chnls)
            print("sampling rate = %s" % samprate)
            print("number of samples = %s" % nsamps)
            print("duration in sec. = %s" % dur)
            print("bitrate = %s" % bitrate)
            print("file format = %s" % format)

        return (chnls, samprate, dur, tableFrac, nsamps, bitrate, format)
    else:
        if CeciliaLib.getVar("DEBUG"):
            print('Unable to get sound infos. "%s" bypassed!' % path)
        return None


def getSoundsFromList(pathList):
    soundDict = dict()
    for path in pathList:
        if os.path.isfile(path):
            infos = getSoundInfo(path)
            if infos is not None:
                sndfile = os.path.split(path)[1]
                if sndfile not in soundDict.keys():
                    soundDict[CeciliaLib.ensureNFD(sndfile)] = {'samprate': infos[1],
                                    'chnls': infos[0],
                                    'dur': infos[2],
                                    'bitrate': infos[5],
                                    'type': infos[6],
                                    'path': path}
        else:
            if CeciliaLib.getVar("DEBUG"):
                print('not a file')
    if CeciliaLib.getVar("DEBUG"):
        print()
    return soundDict
