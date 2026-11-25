#!/usr/bin/env python3
"""ep_push.py: one profiler capture's MoRI EP kernel times into VictoriaMetrics, for the experiment's dashboard.

  python3 tools/ep_push.py results/e2e/<run> prof_<capture> --push http://10.235.192.132:28528 --experiment <exp>

e2e/windows.sh's profile() runs it after each capture. The capture's start and end come from <run>/events.tsv; the
torch-profiler traces written during it (and up to 30 s after its end) are parsed with e2e/e1_analyze.py's
trace_kernels (EpDispatchInterNodeV1Kernel / EpCombineInterNodeV1Kernel durations). Pushed at the capture's middle,
labels capture (MK_1), window (MK) and kernel (dispatch / combine):
  epio_ep_us{stat=mean|p50|p90|p99}   over every call of every rank in the capture, µs
  epio_ep_rank_us{rank}               each rank's mean (rank: the trace's dp<i>_ep<j>), µs
  epio_ep_calls                       calls per rank (92 MoE layers per decode step)
"""
import argparse
import glob
import multiprocessing
import os
import re
import sys
import time

import numpy as np

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(W, "e2e"))
sys.path.insert(0, os.path.join(W, "mb"))
from e1_analyze import _trace_kernels_safe  # noqa: E402
import vmpush  # noqa: E402


def capture_span(run_dir, name):
    a = b = None
    for line in open(os.path.join(run_dir, "events.tsv")):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3 and p[1] == name:
            if p[2] == "start":
                a = float(p[0])
            elif p[2] == "end":
                b = float(p[0])
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("capture", help="prof_<name> as in events.tsv")
    ap.add_argument("--push", required=True)
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--wait", type=float, default=10, help="s to let the trace export settle first")
    a = ap.parse_args()
    time.sleep(a.wait)
    t0, t1 = capture_span(a.run_dir, a.capture)
    if t0 is None or t1 is None:
        sys.exit(f"{a.capture}: no start / end in events.tsv")
    files = [p for p in glob.glob(os.path.join(a.run_dir, "prof", "**", "*.json*"), recursive=True)
             if t0 <= os.path.getmtime(p) <= t1 + 30]
    if not files:
        sys.exit(f"{a.capture}: no traces")
    with multiprocessing.Pool(min(16, len(files))) as pool:
        parsed = pool.map(_trace_kernels_safe, files)
    cap = a.capture[len("prof_"):]
    window = re.sub(r"_\d+$", "", cap)
    p = vmpush.Pusher(a.push, {"experiment": a.experiment, "run": os.path.basename(os.path.normpath(a.run_dir)),
                               "capture": cap, "window": window})
    ts = (t0 + t1) / 2
    for kernel in ("dispatch", "combine"):
        alls, calls = [], []
        for f, k in zip(files, parsed):
            if isinstance(k, str) or not k.get(kernel):
                continue
            v = np.array(k[kernel])
            m = re.search(r"dp(\d+)_pp\d+_tp\d+_dcp\d+_ep(\d+)", os.path.basename(f))
            rank = f"dp{m.group(1)}_ep{m.group(2)}" if m else os.path.basename(f)[:24]
            p.add("epio_ep_rank_us", v.mean(), ts=ts, kernel=kernel, rank=rank)
            alls.append(v)
            calls.append(len(v))
        if not alls:
            continue
        v = np.concatenate(alls)
        p.add("epio_ep_us", v.mean(), ts=ts, kernel=kernel, stat="mean")
        for q in (50, 90, 99):
            p.add("epio_ep_us", np.percentile(v, q), ts=ts, kernel=kernel, stat=f"p{q}")
        p.add("epio_ep_calls", float(np.mean(calls)), ts=ts, kernel=kernel)
        print(f"{cap} {kernel}: {len(alls)} ranks, mean {v.mean():.1f} us, p90 {np.percentile(v, 90):.1f} us", flush=True)
    p.close()
    print(f"{cap}: pushed {p.sent} samples, dropped {p.dropped}", flush=True)


if __name__ == "__main__":
    main()
