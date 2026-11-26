#!/usr/bin/env python3
"""burst_align.py: does a KV burst slow the decode step it lands in? One profiler capture against the NIC counters.

  python3 tools/burst_align.py results/e2e/<run> prof_<capture> --exp yutongwu-<run> --node n08-29 [--out fig.png]

The decode steps come from the capture's TP-rank-0 trace of each DP rank (tools/ep_push.py's trace_steps: wall time,
length, time in EP kernels); --node is DP rank 0's node. The KV comes from ainic-netmon in our VictoriaMetrics: the RX
of every NIC of the node (2 ms) and the ECN-marked packets it received. KV in a step = the bytes of the 2 ms readings
over 100 Gb/s on any NIC of the node (EP alone stays far below). Prints the correlation between the KV in a step and
the step's length and EP time, and draws the node's RX per NIC, its ECN marks, each step's length and each DP rank's
EP time in the step on one time axis. A rank's EP time includes waiting for the other ranks, so the two differ; the
step length is the same on every rank.
"""
import argparse
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ep_push import capture_span, trace_steps  # noqa: E402

V = "http://127.0.0.1:28528"
BURST = 100e9 / 8


def export(exp, host, counter, a, b):
    m = f'netmon_rate{{experiment="{exp}",host="{host}",source="rdma",counter="{counter}",resolution="normal"}}'
    raw = urllib.request.urlopen(V + "/api/v1/export?" + urllib.parse.urlencode({"match[]": m, "start": a, "end": b}),
                                 timeout=120).read().decode()
    out = {}
    for line in raw.splitlines():
        j = json.loads(line)
        t, v = np.array(j["timestamps"]) / 1e3, np.array(j["values"])
        o = np.argsort(t)
        out[j["metric"]["netdev"]] = (t[o], v[o])
    return out


def gapped(t, v, max_dt=0.01):
    """the series with NaN where ainic-netmon has no reading for over max_dt (firmware stalls), so it is not drawn
    across the gap"""
    v = v.astype(float).copy()
    cut = np.where(np.diff(t) > max_dt)[0]
    v[cut + 1] = np.nan          # the reading after a gap is the average over the whole gap
    return np.insert(t, cut + 1, t[cut] + 1e-6), np.insert(v, cut + 1, np.nan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("capture")
    ap.add_argument("--exp", required=True)
    ap.add_argument("--node", required=True, help="the decode node of DP rank 0 (the trace's)")
    ap.add_argument("--out")
    a = ap.parse_args()
    c0, c1 = capture_span(a.run_dir, a.capture)
    tr = {}
    for p in sorted(glob.glob(os.path.join(a.run_dir, "prof", "dp*_tp0_*.json*"))):
        if c0 <= os.path.getmtime(p) <= c1 + 30:
            tr.setdefault(os.path.basename(p).split("_")[0], p)
    st = trace_steps(tr["dp0"]) if "dp0" in tr else []
    if not st:
        sys.exit(f"{a.capture}: no decode steps in DP rank 0's trace")
    other = {k: trace_steps(p) for k, p in tr.items() if k != "dp0"}
    t0, t1 = st[0][0], st[-1][0] + st[-1][1] / 1e3
    rx = export(a.exp, a.node, "rx_rdma_ucast_bytes", t0, t1)
    ecn = export(a.exp, a.node, "rx_rdma_ecn_pkts", t0, t1)

    def kv_in(s0, n):
        got = 0.0
        for t, v in rx.values():
            dt = np.diff(t, prepend=t[0] - 0.002)
            sel = (t > s0) & (t <= s0 + n / 1e3) & (dt < 0.01) & (v > BURST)
            got += float(np.sum(v[sel] * dt[sel]))
        return got / 1e9

    kv = np.array([kv_in(s0, n) for s0, n, _ in st])
    ms, epms = np.array([s[1] for s in st]), np.array([s[2] for s in st])
    corr = lambda x, y: np.corrcoef(x, y)[0, 1] if x.std() > 0 and y.std() > 0 else float("nan")  # noqa: E731
    f = lambda x: f"{x.mean():.1f}" if x.size else "-"  # noqa: E731
    # a step over 1.5x the median is a prefill or a stall, not a decode step: drawn, but left out of the statistics
    ok = ms < 1.5 * np.median(ms)
    k_, m_, e_ = kv[ok], ms[ok], epms[ok]
    hi, no = k_ > np.median(k_), k_ == 0
    print(f"{a.capture}: {len(st)} steps ({(~ok).sum()} long ones left out), step {np.median(m_):.1f} ms, dp0 EP "
          f"{np.median(e_):.1f} ms per step; KV in a step median {np.median(k_):.2f} GB, max {k_.max():.2f}; "
          f"corr(KV, step length) {corr(k_, m_):.2f}, corr(KV, dp0 EP) {corr(k_, e_):.2f}; steps with more KV than the "
          f"median / the rest / without KV ({no.sum()}): length {f(m_[hi])} / {f(m_[~hi])} / {f(m_[no])} ms, dp0 EP "
          f"{f(e_[hi])} / {f(e_[~hi])} / {f(e_[no])} ms")
    for k, so in other.items():
        kk = np.array([kv_in(s0, n) for s0, n, _ in so])
        n_, e = np.array([s[1] for s in so]), np.array([s[2] for s in so])
        o = n_ < 1.5 * np.median(n_)
        kk, e = kk[o], e[o]
        h = kk > np.median(kk)
        print(f"  {k}: EP {np.median(e):.1f} ms per step; corr(KV, {k} EP) {corr(kk, e):.2f}; more KV / the rest "
              f"{f(e[h])} / {f(e[~h])} ms")
    if not a.out:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.style.use("dark_background")
    fig, ax = plt.subplots(4, 1, figsize=(14, 10), sharex=True)
    for nic, (t, v) in sorted(rx.items()):
        tg, vg = gapped(t, v)
        ax[0].step(tg - t0, vg * 8 / 1e9, where="pre", lw=0.6, label=nic)
    ax[0].set_ylabel(f"{a.node} RX Gb/s\nper NIC (2 ms)")
    for t, v in ecn.values():
        tg, vg = gapped(t, v)
        ax[1].step(tg - t0, vg, where="pre", lw=0.6)
    ax[1].set_ylabel(f"ECN-marked pkts/s\n{a.node} received, per NIC")
    for (s0, n, _), k in zip(st, kv):
        ax[2].bar(s0 - t0, n, width=n / 1e3 * 0.9, align="edge", color="tab:orange" if k > 0 else "tab:gray")
    ax[2].set_ylabel("step length (ms)")
    ax[2].set_ylim(0, min(max(ms), 3 * np.median(ms)) * 1.1)
    ranks = [("dp0", st, "tab:orange", "tab:gray")] + [(k, so, "tab:cyan", "slategray") for k, so in other.items()]
    for i, (k, so, c_kv, c_no) in enumerate(ranks):
        w = 0.9 / len(ranks)
        for s0, n, e in so:
            ax[3].bar(s0 - t0 + n / 1e3 * w * i, e, width=n / 1e3 * w, align="edge",
                      color=c_kv if kv_in(s0, n) > 0 else c_no)
    ax[3].set_ylabel("EP kernel time per step (ms)\n" + " | ".join(k for k, *_ in ranks))
    all_e = [e for _, so, *_ in ranks for *_, e in so]
    ax[3].set_ylim(0, min(max(all_e), 3 * np.median(all_e)) * 1.15)
    ax[3].set_xlabel(f"s   (bright: KV reached {a.node} during the step; dp0 is {a.node}, its EP time on the left of each pair)")
    fig.suptitle(f"{os.path.basename(os.path.normpath(a.run_dir))} {a.capture}: corr(KV in a step, step length) = "
                 f"{corr(kv, ms):.2f}, corr(KV, dp0 EP time) = {corr(kv, epms):.2f}")
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print(a.out)


if __name__ == "__main__":
    main()
