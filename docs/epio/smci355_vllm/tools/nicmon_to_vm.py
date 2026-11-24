#!/usr/bin/env python3
"""nicmon_to_vm.py: load a run's nicmon traces (and its windows and step times) into VictoriaMetrics for Grafana.

  python3 tools/nicmon_to_vm.py results/e2e/<run> [--vm http://127.0.0.1:18428] [--bin-ms 10]

Series (labels run, node, nic):
  nic_rx_gbps / nic_tx_gbps      each NIC's RDMA bytes, Gb/s, on a --bin-ms grid (the counters move every ~10.7 ms)
  nic_retx_pps, nic_cnp_pps, nic_ecn_pps, nic_seqerr_pps   per second, from nicmon's slower rows (~100 ms)
  window_on{window}              1 while an IO window's IO runs (events.tsv <name>_io_on .. <name>_io_end), else 0
  step_ms                        the loadgen's median gap between streamed chunks per 10 s record (one decode step)
"""
import argparse
import glob
import json
import os
import re
import sys
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nicmon_load import SLOW, load, rates  # noqa: E402


def push(vm, lines):
    data = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    req = urllib.request.Request(f"{vm}/api/v1/import", data=data, method="POST")
    urllib.request.urlopen(req, timeout=300).read()


def series(name, labels, ts, vals, vm, chunk=60000):
    """one series in pieces of `chunk` points (timestamps in s)"""
    ms = (np.asarray(ts) * 1000).astype(np.int64)
    out = []
    for i in range(0, len(ms), chunk):
        out.append({"metric": {"__name__": name, **labels}, "values": [round(float(v), 3) for v in vals[i:i + chunk]],
                    "timestamps": ms[i:i + chunk].tolist()})
        if len(out) >= 16:
            push(vm, out); out = []
    if out:
        push(vm, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--vm", default="http://127.0.0.1:18428")
    ap.add_argument("--bin-ms", type=float, default=10.0)
    a = ap.parse_args()
    run = os.path.basename(os.path.normpath(a.run_dir))
    b = a.bin_ms / 1e3
    for path in sorted(glob.glob(os.path.join(a.run_dir, "nicmon_*.csv.gz"))):
        node = re.sub(r"^nicmon_|\.csv\.gz$", "", os.path.basename(path))
        tn, fv, st, sv = load(path)
        if len(tn) < 2:
            continue
        grid = np.arange(np.ceil(tn[0, 0] / b) * b, tn[-1, 0], b)
        mid = grid[1:]
        tx, rx = rates(tn, fv, grid)
        for i in range(fv.shape[1] // 2):
            lab = {"run": run, "node": node, "nic": f"ionic_{i}"}
            series("nic_tx_gbps", lab, mid, tx[:, i], a.vm)
            series("nic_rx_gbps", lab, mid, rx[:, i], a.vm)
            if len(st) > 2:
                k = len(SLOW)
                d = np.diff(sv[:, i * k:(i + 1) * k], axis=0) / np.diff(st)[:, None]
                for name, col in (("nic_retx_pps", 0), ("nic_cnp_pps", 3), ("nic_ecn_pps", 4), ("nic_seqerr_pps", 5)):
                    series(name, lab, st[1:], d[:, col], a.vm)
        print(f"{node}: {len(tn)} counter rows, {len(grid) - 1} bins of {a.bin_ms:g} ms", flush=True)
    ev = os.path.join(a.run_dir, "events.tsv")
    if os.path.exists(ev):
        on = {}
        for line in open(ev):
            p = line.rstrip("\n").split("\t")
            if len(p) < 2:
                continue
            m = re.match(r"^(.+)_io_(on|end)$", p[1])
            if m:
                on.setdefault(m.group(1), []).append((float(p[0]), m.group(2)))
        for w, marks in on.items():
            ts, vals = [], []
            for t, kind in marks:
                ts += [t - 0.001, t]; vals += [0, 1] if kind == "on" else [1, 0]
            series("window_on", {"run": run, "window": w}, ts, vals, a.vm)
    live = os.path.join(a.run_dir, "load", "live.jsonl")
    if os.path.exists(live):
        ts, vals = [], []
        for line in open(live):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("gap_med_ms"):
                ts.append(r["t"]); vals.append(r["gap_med_ms"])
        if ts:
            series("step_ms", {"run": run}, ts, vals, a.vm)
    print(f"loaded {run} into {a.vm}")


if __name__ == "__main__":
    main()
