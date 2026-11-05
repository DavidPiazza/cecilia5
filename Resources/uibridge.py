"""
Hand-off point between the audio engine and the wx interface.

pyo calls back into Python from its audio thread (clock, VU meter, automation
and MIDI/OSC widget updates). Those callbacks must not touch wx widgets, and
posting a wx.CallAfter for every one of them floods the event queue. Instead
they post here:

    latest(key, func, *args)  keep only the most recent call per key
                              (values that are redrawn: clock, meters, sliders)
    event(func, *args)        run every call, in order (state changes: stop,
                              close a dialog, MIDI learn result)

A wx.Timer on the main thread (install()) runs the pending calls about 30
times per second. Calls posted from the main thread itself are queued the same
way, so ordering is consistent wherever they come from.
"""
import threading
import traceback

import wx

INTERVAL_MS = 33

_lock = threading.Lock()
_latest = {}      # key -> (func, args); insertion-ordered
_events = []      # [(func, args)]
_timer = None


def latest(key, func, *args):
    with _lock:
        _latest.pop(key, None)  # re-insert so the newest update runs last
        _latest[key] = (func, args)


def event(func, *args):
    with _lock:
        _events.append((func, args))


def clear_latest():
    """Drop pending redraws (e.g. after stop, so a late clock update can't
    overwrite the reset clock)."""
    with _lock:
        _latest.clear()


def flush():
    """Run everything pending now. Main thread only."""
    global _latest, _events
    with _lock:
        if not _latest and not _events:
            return
        latest_calls, event_calls = _latest, _events
        _latest, _events = {}, []
    for func, args in list(latest_calls.values()) + event_calls:
        try:
            func(*args)
        except RuntimeError:
            # wrapped C++ widget already deleted (interface was rebuilt)
            pass
        except Exception:
            traceback.print_exc()


def install(window):
    """Start draining the bridge on the main thread. Call once, with a window
    that lives as long as the app (the main frame)."""
    global _timer
    if _timer is not None:
        return
    _timer = wx.Timer(window)
    window.Bind(wx.EVT_TIMER, lambda evt: flush(), _timer)
    _timer.Start(INTERVAL_MS)
