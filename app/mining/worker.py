"""Isolated worker process for expensive mining computations.

Heavy PM4Py work never runs in the web server's request path or threads: it
runs in a separate process (``python -m app.mining.worker``). Python cannot
interrupt a computation inside a thread, so cancellation and timeouts
terminate the worker process; a fresh worker starts for the next task. One
task runs at a time.

Protocol: length-prefixed pickles over the child's stdin/stdout. The child
moves its original stdout aside and points file descriptor 1 at /dev/null so
output printed by PM4Py or native solvers cannot corrupt the protocol stream.
"""

from __future__ import annotations

import logging
import os
import pickle
import queue
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger("processlens.mining.worker")
_HEADER = struct.Struct(">Q")


class TaskFailed(Exception):
    def __init__(self, key: str, params: dict | None = None):
        super().__init__(key)
        self.key = key
        self.params = params or {}


class TaskCancelled(Exception):
    pass


class TaskTimedOut(Exception):
    pass


def _send(stream, obj) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(_HEADER.pack(len(data)))
    stream.write(data)
    stream.flush()


def _recv(stream):
    head = stream.read(_HEADER.size)
    if not head or len(head) < _HEADER.size:
        raise EOFError
    (n,) = _HEADER.unpack(head)
    data = stream.read(n)
    if len(data) < n:
        raise EOFError
    return pickle.loads(data)  # noqa: S301 - trusted channel between our own processes


# --------------------------------------------------------------------------- child
def main() -> None:  # pragma: no cover - runs in the child process
    os.environ.setdefault("TQDM_DISABLE", "1")
    proto_out = os.fdopen(os.dup(1), "wb")
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115
    proto_in = sys.stdin.buffer
    import warnings

    warnings.filterwarnings("ignore")
    try:
        import pm4py  # noqa: F401  (warm-up: importing PM4Py takes several seconds)
    except Exception:
        pass
    from app.mining.tasks import TASKS, TaskError

    last = [0.0]

    def report(stage_key: str, fraction: float | None = None, **params) -> None:
        now = time.monotonic()
        if now - last[0] < 0.15 and fraction not in (None, 1.0):
            return
        last[0] = now
        _send(proto_out, ("progress", stage_key, fraction, params))

    _send(proto_out, ("ready",))
    while True:
        try:
            msg = _recv(proto_in)
        except EOFError:
            return
        if msg is None:
            return
        kind, payload = msg
        try:
            _send(proto_out, ("result", TASKS[kind](payload, report)))
        except TaskError as exc:
            _send(proto_out, ("error", exc.key, exc.params))
        except MemoryError:
            _send(proto_out, ("error", "run.err.memory", {}))
        except Exception as exc:
            _send(proto_out, ("error", "run.err.exception", {"error": f"{type(exc).__name__}: {str(exc)[:500]}"}))


# --------------------------------------------------------------------------- parent
class MiningWorker:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._messages: queue.Queue = queue.Queue()
        self._lock = threading.Lock()

    def _reader(self, proc: subprocess.Popen, out: queue.Queue) -> None:
        try:
            while True:
                out.put(_recv(proc.stdout))
        except (EOFError, OSError, ValueError, pickle.UnpicklingError):
            out.put(("eof",))

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        from app.config import PROJECT_ROOT

        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["TQDM_DISABLE"] = "1"
        env["PYTHONHASHSEED"] = "0"  # deterministic set/dict iteration (reproducible randomized miners)
        self._messages = queue.Queue()
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "app.mining.worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(PROJECT_ROOT), env=env)
        threading.Thread(target=self._reader, args=(self._proc, self._messages), daemon=True,
                         name="processlens-mining-reader").start()
        log.info("Started mining worker pid=%s", self._proc.pid)

    def kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except Exception:
                pass
        log.info("Stopped mining worker")

    def run(self, kind: str, payload: dict, timeout_s: float, cancelled: Callable[[], bool],
            on_progress: Callable[[str, float | None, dict], None]) -> dict:
        """Run one task. Raises TaskFailed / TaskCancelled / TaskTimedOut."""
        with self._lock:
            self._ensure()
            deadline = time.monotonic() + timeout_s
            try:
                _send(self._proc.stdin, (kind, payload))
            except (BrokenPipeError, OSError):
                self.kill()
                raise TaskFailed("run.err.worker_died") from None
            while True:
                if cancelled():
                    self.kill()
                    raise TaskCancelled()
                if time.monotonic() > deadline:
                    self.kill()
                    raise TaskTimedOut()
                try:
                    msg = self._messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                tag = msg[0]
                if tag == "ready":
                    continue
                if tag == "progress":
                    on_progress(msg[1], msg[2], msg[3] or {})
                elif tag == "result":
                    return msg[1]
                elif tag == "error":
                    raise TaskFailed(msg[1], msg[2])
                else:  # "eof": the worker exited unexpectedly (crash, killed by the OS, ...)
                    self.kill()
                    raise TaskFailed("run.err.worker_died")


worker = MiningWorker()

if __name__ == "__main__":  # pragma: no cover
    main()
