"""vmpush.py: samples into VictoriaMetrics (Prometheus text format with millisecond timestamps,
/api/v1/import/prometheus) from a background thread, for the experiment's Grafana dashboard (tools/epio_dash.py).

  p = Pusher("http://10.235.192.132:28528", {"experiment": "yutongwu-e1x_7248"})
  p.add("epio_kv_ms", 23.8, kind="transfer", dev=3)      # at time.time(); ts= for another time (s)
  p.close()                                              # the last batch out

Only the standard library. Never raises into the caller and never blocks it: a failed post drops that batch (counted
in .dropped) and the next one goes on.
"""
import gzip
import threading
import time
import urllib.request


def _esc(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Pusher:
    def __init__(self, url, labels, every=1.0):
        self.url = url.rstrip("/") + "/api/v1/import/prometheus"
        self.base = dict(labels)
        self.every = every
        self.buf = []
        self.lock = threading.Lock()
        self.dropped = 0
        self.sent = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def add(self, metric, value, /, ts=None, **labels):
        lab = ",".join(f'{k}="{_esc(v)}"' for k, v in {**self.base, **labels}.items())
        line = f"{metric}{{{lab}}} {float(value):.6g} {int(round((time.time() if ts is None else ts) * 1000))}"
        with self.lock:
            self.buf.append(line)

    def flush(self):
        with self.lock:
            lines, self.buf = self.buf, []
        if not lines:
            return
        body = gzip.compress(("\n".join(lines) + "\n").encode(), 3)
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Encoding": "gzip", "Content-Type": "text/plain"})
        try:
            urllib.request.urlopen(req, timeout=10).read()
            self.sent += len(lines)
        except Exception:  # noqa: BLE001
            self.dropped += len(lines)

    def _loop(self):
        while not self._stop.wait(self.every):
            self.flush()

    def close(self):
        self._stop.set()
        self._t.join(timeout=5)
        self.flush()


def from_args(url, labels_csv, **extra):
    """--push URL --push-labels k=v,k=v: a Pusher, or None without a URL"""
    if not url:
        return None
    labels = dict(kv.split("=", 1) for kv in (labels_csv or "").split(",") if "=" in kv)
    return Pusher(url, {**labels, **extra})
