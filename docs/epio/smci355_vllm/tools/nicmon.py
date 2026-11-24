#!/usr/bin/env python3
"""nicmon.py: RDMA byte counters of every ionic NIC at ~10 ms resolution (runs on a node, read-only).

The firmware refreshes the counters about every 10.7 ms. With the RDMA core's cache of them shortened to 1 ms
(hw_counters/lifespan, e2e/e1.sh NICMON=1) every read fetches them from the NIC, which takes ~1.3 ms under load and
now and then ~95 ms, so each NIC gets its own read time (taken after its read). A row is written only when a byte
counter moved; a slower row (every --slow-ms) carries the retransmission / CNP / ECN / sequence-error counters.

  python3 nicmon.py --out /path/nicmon_<node>.csv.gz [--duration 3600]   (SIGTERM / SIGINT end it cleanly)

fast rows:  f,<unix time>,<tx_bytes ionic_0>,<rx_bytes ionic_0>,...,<tx_bytes ionic_7>,<rx_bytes ionic_7>,
            <read time of ionic_0 - unix time, us>,...,<ionic_7's>   (older traces: no read times)
slow rows:  s,<unix time>,<SLOW counters ionic_0>,...  (header line '# slow ...' lists them)
"""
import argparse
import glob
import gzip
import os
import signal
import time

FAST = ("tx_rdma_ucast_bytes", "rx_rdma_ucast_bytes")
SLOW = ("tx_rdma_retx_pkts", "tx_rdma_retx_bytes", "tx_rdma_ack_timeout", "rx_rdma_cnp_pkts", "rx_rdma_ecn_pkts",
        "req_rx_pkt_seq_err", "resp_rx_outouf_seq", "rx_rdma_ucast_pkts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--poll-ms", type=float, default=1.0)
    ap.add_argument("--slow-ms", type=float, default=100.0)
    ap.add_argument("--duration", type=float, default=0, help="seconds; 0 = until signalled")
    a = ap.parse_args()

    devs = sorted(glob.glob("/sys/class/infiniband/ionic_*"), key=lambda d: int(d.rsplit("_", 1)[1]))
    hw = [f"{d}/ports/1/hw_counters" for d in devs]
    fast = [open(f"{h}/{k}", "rb", buffering=0) for h in hw for k in FAST]
    slow = [open(f"{h}/{k}", "rb", buffering=0) for h in hw for k in SLOW]

    def read(fs):
        out = []
        for f in fs:
            f.seek(0)
            out.append(f.read().strip().decode())
        return out

    def read_fast(t0):
        """the byte counters, and each NIC's read time after t0 (us)"""
        out, offs = [], []
        for i in range(0, len(fast), len(FAST)):
            out += read(fast[i:i + len(FAST)])
            offs.append(str(int((time.time() - t0) * 1e6)))
        return out, offs

    stop = []
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.append(1))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with gzip.open(a.out, "wt", compresslevel=3) as w:
        w.write(f"# host {os.uname().nodename} devs {' '.join(os.path.basename(d) for d in devs)}\n")
        w.write(f"# fast {' '.join(FAST)} per dev, then each dev's read time - row time (us)\n"
                f"# slow {' '.join(SLOW)} per dev\n")
        t_end = time.time() + a.duration if a.duration > 0 else float("inf")
        poll, slow_s = a.poll_ms / 1e3, a.slow_ms / 1e3
        last, next_slow, next_flush = None, 0.0, time.time() + 5
        while not stop and time.time() < t_end:
            t = time.time()
            v, offs = read_fast(t)
            if v != last:
                w.write(f"f,{t:.6f},{','.join(v)},{','.join(offs)}\n")
                last = v
            if t >= next_slow:
                w.write(f"s,{t:.6f},{','.join(read(slow))}\n")
                next_slow = t + slow_s
            if t >= next_flush:  # a quiet node's rows compress to almost nothing and would sit in zlib until the end
                w.flush()
                next_flush = t + 5
            d = poll - (time.time() - t)
            if d > 0:
                time.sleep(d)


if __name__ == "__main__":
    main()
