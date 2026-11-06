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
The audio engine's process.

The interface (engine_client.RemoteEngine) starts this process and connects
to it with two connections:

  commands  interface -> engine: (id, name, args, kwargs, wantReply)
            Each command is a method of the AudioServer (AudioServer.COMMANDS).
            When wantReply is true the engine answers with
            ("reply", id, ok, result, running).
  messages  engine -> interface: replies and
            ("latest", key, args)   a value to display (newest wins)
            ("event", name, args)   something that happened, in order
            ("clear",)              drop pending values
            ("state", running)      the server started or stopped

Started with:  python -m Resources.engine_process ADDRESS
       or:     Cecilia5 --cecilia-engine ADDRESS   (the app bundle)
The connection key is in the CECILIA_ENGINE_KEY environment variable.
"""
import os
import sys
import threading
import time
import traceback
from multiprocessing.connection import Client

ENGINE_ARG = "--cecilia-engine"
SEND_INTERVAL = 0.015     # values to display are sent at most this often
OFFLINE_INTERVAL = 0.1    # progress while rendering offline (see _PipeSink.latest)


class _PipeSink:
    """Engine events -> the interface process.

    Called from the audio thread, pyo's offline render thread and the command
    loop. Values to display are kept per key (newest wins) and sent by a
    sender thread about 60 times per second, with the events in order. The
    sender also watches the server, and tells the interface when it starts or
    stops (an offline render ends on its own)."""
    def __init__(self, conn):
        self.conn = conn
        self.lock = threading.Lock()      # pending values / events
        self.sendLock = threading.Lock()  # the connection
        self.pending = []                 # ordered messages
        self.latestValues = {}
        self.server = None                # the AudioServer, once created
        self.running = False              # last running state sent
        self.stopRequested = False        # set by the stop command
        self.lastOffline = 0.0

    # --- AudioServer's sink interface ---
    def latest(self, key, *args):
        with self.lock:
            self.latestValues.pop(key, None)
            self.latestValues[key] = args
        # pyo's offline render keeps the interpreter for itself most of the
        # time, so the sender thread rarely runs. Progress is sent from the
        # render thread itself.
        if self.server is not None and not self.server.cfg["toDac"]:
            now = time.monotonic()
            if now - self.lastOffline >= OFFLINE_INTERVAL:
                self.lastOffline = now
                self.flush()

    def event(self, name, *args):
        with self.lock:
            self.pending.append(("event", name, args))

    def clear(self):
        with self.lock:
            self.latestValues.clear()
            self.pending.append(("clear",))

    # --- sending ---
    def _take(self):
        with self.lock:
            msgs = self.pending
            msgs.extend(("latest", key, args) for key, args in self.latestValues.items())
            self.pending = []
            self.latestValues = {}
        return msgs

    def _checkState(self, msgs):
        """Adds a state message if the server started or stopped since the
        last one. Called with sendLock held."""
        if self.server is None:
            return
        running = bool(self.server.isRunning())
        if running != self.running:
            self.running = running
            msgs.append(("state", running))
            if not running and not self.stopRequested and not self.server.cfg["toDac"]:
                # An offline render (bounce, batch) finished.
                msgs.append(("event", "end", ()))

    def flush(self, reply=None):
        """Sends everything pending (and a reply after it, so the interface
        has seen every earlier event when it gets the reply)."""
        with self.sendLock:
            msgs = self._take()
            self._checkState(msgs)
            if reply is not None:
                running = bool(self.server.isRunning()) if self.server is not None else False
                if running != self.running:
                    self.running = running
                reply = reply + (running,)
                msgs.append(reply)
            for msg in msgs:
                self.conn.send(msg)

    def run(self):
        while True:
            time.sleep(SEND_INTERVAL)
            try:
                self.flush()
            except (OSError, EOFError):
                os._exit(0)  # the interface is gone
            except Exception:
                traceback.print_exc()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == ENGINE_ARG:
        argv = argv[1:]
    address = argv[0]
    key = bytes.fromhex(os.environ.pop("CECILIA_ENGINE_KEY"))
    commands = Client(address, authkey=key)
    messages = Client(address, authkey=key)

    sink = _PipeSink(messages)
    threading.Thread(target=sink.run, name="engine-sender", daemon=True).start()

    # The first message is the engine configuration.
    cfg = commands.recv()
    from . import audio
    server = audio.AudioServer(sink, cfg)
    server.deferOfflineEnd = True  # the sink reports the end of offline renders
    sink.server = server
    allowed = set(audio.AudioServer.COMMANDS)

    while True:
        try:
            msg = commands.recv()
        except (EOFError, OSError):
            break  # the interface is gone
        ident, name, args, kwargs, wantReply = msg
        if name == "_quit":
            break
        if name not in allowed:
            print("Cecilia engine: unknown command %r" % name)
            continue
        if name == "stop":
            sink.stopRequested = True
        elif name in ("play", "midiLearn"):
            sink.stopRequested = False
        try:
            result = getattr(server, name)(*args, **kwargs)
            ok = True
        except Exception:
            traceback.print_exc()
            result = traceback.format_exc()
            ok = False
        if wantReply:
            try:
                sink.flush(("reply", ident, ok, result))
            except (OSError, EOFError):
                break
    try:
        if server.isRunning():
            server.server.stop()
        server.server.shutdown()
    except Exception:
        pass
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
