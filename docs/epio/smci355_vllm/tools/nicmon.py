#!/usr/bin/env python3
"""nicmon.py: RDMA byte counters of every ionic NIC at a few ms resolution (runs on a node, read-only).

With the RDMA core's cache of the counters shortened to 1 ms (hw_counters/lifespan, e2e/e1.sh NICMON=1), every read
fetches them from the NIC: ~1.3 ms under load, now and then ~95 ms. Read one NIC after another, that gave each NIC a
fresh value only every ~13 ms; so one thread per NIC reads its two byte counters (then sleeps --poll-ms), and each
sample carries its own read time. A slower row (every --slow-ms) carries the retransmission / CNP / ECN /
sequence-error counters.

  python3 nicmon.py --out /path/nicmon_<node>.csv.gz [--duration 3600]   (SIGTERM / SIGINT end it cleanly)

per-NIC rows: n,<NIC index>,<read time>,<tx_bytes>,<rx_bytes>      (written when either moved)
slow rows:    s,<unix time>,<SLOW counters ionic_0>,...            (header line '# slow ...' lists them)
(older traces: f,<time>,<tx_bytes ionic_0>,<rx_bytes ionic_0>,...[,<each NIC's read time - row time, us>])
"""
import argparse
import glob
import gzip
import os
import queue
import signal
import threading
import time

FAST = ("tx_rdma_ucast_bytes", "rx_rdma_ucast_bytes")
SLOW = ("tx_rdma_retx_pkts", "tx_rdma_retx_bytes", "tx_rdma_ack_timeout", "rx_rdma_cnp_pkts", "rx_rdma_ecn_pkts",
        "req_rx_pkt_seq_err", "resp_rx_outouf_seq", "rx_rdma_ucast_pkts")


def read(fs):
    out = []
    for f in fs:
        f.seek(0)
        out.append(f.read().strip().decode())
    return out


def nic_loop(i, fs, q, stop, poll):
    last = None
    while not stop.is_set():
        v = read(fs)
        t = time.time()
        if v != last:
            q.put(f"n,{i},{t:.6f},{v[0]},{v[1]}\n")
            last = v
        if poll > 0:
            time.sleep(poll)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--poll-ms", type=float, default=1.0, help="each NIC thread's sleep between reads")
    ap.add_argument("--slow-ms", type=float, default=100.0)
    ap.add_argument("--duration", type=float, default=0, help="seconds; 0 = until signalled")
    a = ap.parse_args()

    devs = sorted(glob.glob("/sys/class/infiniband/ionic_*"), key=lambda d: int(d.rsplit("_", 1)[1]))
    hw = [f"{d}/ports/1/hw_counters" for d in devs]
    slow = [open(f"{h}/{k}", "rb", buffering=0) for h in hw for k in SLOW]

    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    q = queue.SimpleQueue()
    threads = [threading.Thread(target=nic_loop, daemon=True,
                                args=(i, [open(f"{h}/{k}", "rb", buffering=0) for k in FAST], q, stop, a.poll_ms / 1e3))
               for i, h in enumerate(hw)]

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with gzip.open(a.out, "wt", compresslevel=3) as w:
        w.write(f"# host {os.uname().nodename} devs {' '.join(os.path.basename(d) for d in devs)}\n")
        w.write(f"# per-NIC rows: n,<NIC>,<read time>,{','.join(FAST)}\n# slow {' '.join(SLOW)} per dev\n")
        for th in threads:
            th.start()
        t_end = time.time() + a.duration if a.duration > 0 else float("inf")
        next_slow, next_flush = 0.0, time.time() + 5
        while not stop.is_set() and time.time() < t_end:
            t = time.time()
            if t >= next_slow:
                w.write(f"s,{t:.6f},{','.join(read(slow))}\n")
                next_slow = t + a.slow_ms / 1e3
            try:
                while True:
                    w.write(q.get_nowait())
            except queue.Empty:
                pass
            if t >= next_flush:  # a quiet node's rows compress to almost nothing and would sit in zlib until the end
                w.flush()
                next_flush = t + 5
            time.sleep(0.005)
        stop.set()
        for th in threads:
            th.join(timeout=1)
        try:
            while True:
                w.write(q.get_nowait())
        except queue.Empty:
            pass


if __name__ == "__main__":
    main()
