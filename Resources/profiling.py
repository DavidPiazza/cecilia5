"""
Development-only responsiveness profiling. Enabled with CECILIA_PROFILE=1.

A 5 ms wx.Timer on the main thread measures how late each tick fires. A late
tick means the GUI thread was busy (or waiting for the GIL) for that long, so
the lateness is a direct measure of how frozen the interface was.

Also records named intervals (e.g. Play click -> audio server started).
"""
import os
import time

import wx

ENABLED = os.environ.get("CECILIA_PROFILE", "") not in ("", "0")
TICK_MS = 5

_stalls = []          # ms of lateness per tick since the last reset()
_marks = {}           # name -> start time
_intervals = []       # (name, ms)
_timer = None
_last = None


def _on_tick(evt):
    global _last
    now = time.perf_counter()
    if _last is not None:
        _stalls.append(max(0.0, (now - _last) * 1000.0 - TICK_MS))
    _last = now


def install(window):
    """Start the heartbeat timer (owned by `window`). No-op unless enabled."""
    global _timer
    if not ENABLED or _timer is not None:
        return
    _timer = wx.Timer(window)
    window.Bind(wx.EVT_TIMER, _on_tick, _timer)
    _timer.Start(TICK_MS)
    print("CECILIA_PROFILE: heartbeat every %d ms" % TICK_MS, flush=True)


def reset():
    global _last
    del _stalls[:]
    del _intervals[:]
    _last = time.perf_counter()  # a stall right after reset() counts too


def mark(name):
    if ENABLED:
        _marks[name] = time.perf_counter()


def done(name):
    if ENABLED and name in _marks:
        ms = (time.perf_counter() - _marks.pop(name)) * 1000.0
        _intervals.append((name, ms))
        print("CECILIA_PROFILE: %s %.1f ms" % (name, ms), flush=True)


def stats():
    """Summary of stalls since the last reset()."""
    s = sorted(_stalls)
    if not s:
        return {"ticks": 0, "max_ms": 0.0, "p95_ms": 0.0, "over_50ms": 0, "frozen_ms": 0.0}
    p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
    return {
        "ticks": len(s),
        "max_ms": round(s[-1], 1),
        "p95_ms": round(p95, 1),
        "over_50ms": sum(1 for x in s if x > 50),
        "frozen_ms": round(sum(x for x in s if x > 50), 1),
        "intervals": [(n, round(ms, 1)) for n, ms in _intervals],
    }
