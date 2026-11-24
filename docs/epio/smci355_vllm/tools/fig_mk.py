#!/usr/bin/env python3
"""fig_mk.py: figures of the Moonshot-shaped KV runs (tools/mk_analyze.py's windows, e2e/e1_analyze.py's EP kernels).

  python3 tools/fig_mk.py traffic <run_dir> <window> <out.png> [--node n08-21] [--zoom-at 30] [--zoom-s 0.8]
  python3 tools/fig_mk.py impact <out.png> <label>=<run_dir>[:w1,w2,...] ...

traffic: the node's per-NIC RX / TX like the decode-side screenshots (Gb/s, 10 ms), 60 s of the window's IO and a zoom.
impact:  per window, the decode step's change against the IO-off stretches around it and the change of the MoRI EP
         dispatch kernel (mean of the ranks' means) against the run's base windows.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mk_analyze as mk  # noqa: E402
from nicmon_load import load, rates  # noqa: E402


def dark():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.style.use("dark_background")
    return plt


def traffic(a):
    plt = dark()
    ws = {w[0]: w for w in mk.windows(a.run_dir)}
    _, t0, t1, _, _ = ws[a.window]
    tn, v, _, _ = load(os.path.join(a.run_dir, f"nicmon_{a.node}.csv.gz"))
    fig, ax = plt.subplots(2, 2, figsize=(16, 7.5))
    for row, (s, e) in enumerate(((t0, min(t1, t0 + 60)), (t0 + a.zoom_at, t0 + a.zoom_at + a.zoom_s))):
        g = np.arange(s, e, 0.01)
        tx, rx = rates(tn, v, g)
        x = g[:-1] - g[0]
        for i in range(8):
            ax[row][0].plot(x, rx[:, i], lw=0.7, label=f"ionic_{i}")
            ax[row][1].plot(x, tx[:, i], lw=0.7, label=f"ionic_{i}")
        span = f"{e - s:g} s" if row else "60 s"
        ax[row][0].set_title(f"{a.node} port_rcv_data, Gb/s per NIC, 10 ms ({span})")
        ax[row][1].set_title(f"{a.node} port_xmit_data, Gb/s per NIC, 10 ms ({span})")
        for c in ax[row]:
            c.set_ylim(bottom=0); c.grid(alpha=0.2); c.set_xlabel("s")
    ax[0][1].legend(fontsize=7, ncol=4, loc="upper right")
    fig.suptitle(f"{os.path.basename(os.path.normpath(a.run_dir))} {a.window}", fontsize=10)
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print(a.out)


def impact(a):
    plt = dark()
    groups = []
    for spec in a.runs:
        label, rest = spec.rsplit("=", 1)
        rd, _, only = rest.partition(":")
        ws = mk.windows(rd)
        lv = mk.live(rd)
        cap = json.load(open(os.path.join(rd, "summary.json"))).get("captures", {})
        disp = {k.rsplit("_", 1)[0]: np.mean([c["dispatch_mean"] for c in v]) for k, v in cap.items() if v}
        base_d = np.mean([d for k, d in disp.items() if k == "base"]) if "base" in disp else np.nan
        rows = []
        for i, w in enumerate(ws):
            if w[0].startswith("base") or (only and w[0] not in only.split(",")):
                continue
            step = (mk.steps(lv, w[1], w[2]) / mk.local_base(lv, ws, i) - 1) * 100
            rows.append((w[0], step, (disp.get(w[0], np.nan) / base_d - 1) * 100))
        groups.append((label, rows))
    n = len(groups)
    fig, ax = plt.subplots(n, 1, figsize=(14, 3.6 * n), squeeze=False)
    for (label, rows), c in zip(groups, ax[:, 0]):
        x = np.arange(len(rows))
        c.bar(x - 0.2, [r[1] for r in rows], 0.4, label="decode step", color="#4ea1ff")
        c.bar(x + 0.2, [r[2] for r in rows], 0.4, label="EP dispatch (mean)", color="#ff9f43")
        for xi, r in zip(x, rows):
            c.text(xi - 0.2, r[1], f"{r[1]:+.1f}", ha="center", va="bottom", fontsize=7)
            c.text(xi + 0.2, r[2], f"{r[2]:+.0f}", ha="center", va="bottom", fontsize=7)
        c.set_xticks(x); c.set_xticklabels([r[0] for r in rows], fontsize=8)
        c.axhline(0, color="w", lw=0.5); c.grid(alpha=0.2, axis="y")
        c.set_ylabel("% vs no IO"); c.set_title(label, fontsize=10); c.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print(a.out)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("traffic")
    t.add_argument("run_dir"); t.add_argument("window"); t.add_argument("out")
    t.add_argument("--node", default="n08-21"); t.add_argument("--zoom-at", type=float, default=30)
    t.add_argument("--zoom-s", type=float, default=0.8)
    i = sub.add_parser("impact")
    i.add_argument("out"); i.add_argument("runs", nargs="+")
    a = ap.parse_args()
    traffic(a) if a.cmd == "traffic" else impact(a)


if __name__ == "__main__":
    main()
