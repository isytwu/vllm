#!/usr/bin/env python3
"""mk_analyze.py: the Moonshot-shaped KV runs (MK windows of e2e/windows.sh) from nicmon traces and io_inject records.

  python3 tools/mk_analyze.py results/e2e/<run> [--plots DIR] [--zoom-s 0.8]

Per window (IO on = events <w>_io_on - 20 s .. <w>_io_end; base = the base windows):
  step     the loadgen's median gap between streamed chunks (one decode step), vs the base windows
  EP       decode TX per NIC: mean, and p50 / p99 of the bins (decode nodes send only EP and READ requests)
  KV       io_inject: transfers, duration p50 / p99 (start to done), latency p50 / p99 (arrival to done, with the wait
           for the previous transfer), rate per NIC while transferring; decode RX bins p99 / max
  loss     retransmitted packets / s at the senders, sequence errors / s at the receivers (TC 0 drops)
  CNP      CNPs / s at the decode nodes (EP packets marked in a switch queue)
Plots: Moonshot-style per-NIC RX / TX (--bin-ms bins, default 5) of the first decode node, an overview of the window and a zoom.
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nicmon_load import SLOW, load as load_nicmon, rates  # noqa: E402


BIN = 0.005


def bins(series, t0, t1, b=None):
    """Gb/s per NIC on BIN-second bins in [t0, t1): (tx [n, 8], rx [n, 8], bin starts)"""
    grid = np.arange(t0, t1, b or BIN)
    if len(grid) < 2 or not any(len(s[0]) > 1 for s in series):
        return None, None, grid
    tx, rx = rates(series, grid)
    return tx, rx, grid[:-1]


def slow_rate(st, sv, t0, t1, col):
    """per second over [t0, t1], summed over the 8 NICs, of SLOW counter col"""
    m = (st >= t0) & (st <= t1)
    if m.sum() < 2:
        return float("nan")
    k = len(SLOW)
    s = sv[m][:, col::k].sum(1)
    return float((s[-1] - s[0]) / (st[m][-1] - st[m][0]))


def windows(run_dir):
    """[(name, IO-on start, IO-on end, window start, window end)]; base windows: (base<i>, start + 20, end, start, end)"""
    on, end, st, en = {}, {}, {}, {}
    for line in open(os.path.join(run_dir, "events.tsv")):
        p = line.rstrip("\n").split("\t")
        if len(p) < 3:
            continue
        t, name, what = float(p[0]), p[1], p[2]
        m = re.match(r"^(.+)_io_(on|end)$", name)
        if m:
            (on if m.group(2) == "on" else end).setdefault(m.group(1), t)
        elif what in ("start", "end") and re.match(r"^(base|MK\w*|I[WR]\w+)$", name):
            (st if what == "start" else en).setdefault(name, []).append(t)
    out = []
    for w in on:
        if w in end and w in st and w in en:
            out.append((w, on[w] - 20, end[w], st[w][0], en[w][0]))
    for i, (a, b) in enumerate(zip(st.get("base", []), en.get("base", []))):
        out.append((f"base{i + 1}", a + 20, b, a, b))
    return sorted(out, key=lambda x: x[1])


def live(run_dir):
    out = []
    for line in open(os.path.join(run_dir, "load", "live.jsonl")):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("gap_med_ms"):
            out.append((r["t"], r["gap_med_ms"]))
    return np.array(out)


def steps(lv, t0, t1):
    """median step of the loadgen's 10 s records that end in [t0 + 10, t1]"""
    m = (lv[:, 0] >= t0 + 10) & (lv[:, 0] <= t1)
    return float(np.median(lv[m, 1])) if m.any() else float("nan")


def local_base(lv, ws, i, span=60):
    """median step of the IO-off records up to span s before window i starts and after it ends (the decode's
    contexts grow all run long, so the step drifts up; each window is compared with its own surroundings)"""
    _, _, _, s, e = ws[i]
    busy = [(w[3], w[4] + 8) for w in ws if not w[0].startswith("base")]
    m = ((lv[:, 0] >= s - span) & (lv[:, 0] <= s)) | ((lv[:, 0] >= e + 18) & (lv[:, 0] <= e + span))
    for a, b in busy:
        m &= ~((lv[:, 0] > a) & (lv[:, 0] < b + 10))
    return float(np.median(lv[m, 1])) if m.any() else float("nan")


def io_records(run_dir, w):
    """io_inject's KV records of window w (every sender of it)"""
    dur, lat, rate, n = [], [], [], 0
    for f in glob.glob(os.path.join(run_dir, f"inj_{w}_1", "*", "inj", "send", "io_dev*.npz")):
        z = np.load(f)
        if "kv_start" not in z.files or not z["kv_start"].size:
            continue
        d = z["kv_done"] - z["kv_start"]
        dur += list(d * 1e3); lat += list((z["kv_done"] - z["kv_arrive"]) * 1e3)
        rate += list(z["kv_bytes"] / d / 1e9); n += z["kv_start"].size
    pct = lambda x, q: float(np.percentile(x, q)) if len(x) else float("nan")  # noqa: E731
    return dict(n=n, dur50=pct(dur, 50), dur99=pct(dur, 99), lat50=pct(lat, 50), lat99=pct(lat, 99),
                rate50=pct(rate, 50))


def plot(path, title, t, tx, rx, nic_line=400):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    fig, ax = plt.subplots(1, 2, figsize=(16, 4.2))
    x = t - t[0]
    for i in range(rx.shape[1]):
        ax[0].plot(x, rx[:, i], lw=0.8, label=f"ionic_{i}")
        ax[1].plot(x, tx[:, i], lw=0.8, label=f"ionic_{i}")
    ax[0].set_title(f"{title}: port_rcv_data (Gb/s, {BIN * 1e3:g} ms)")
    ax[1].set_title(f"{title}: port_xmit_data (Gb/s, {BIN * 1e3:g} ms)")
    for a in ax:
        a.set_xlabel("s"); a.grid(alpha=0.2); a.set_ylim(bottom=0)
    ax[0].axhline(nic_line, color="w", lw=0.5, ls=":")
    ax[1].legend(fontsize=7, ncol=4, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--plots")
    ap.add_argument("--zoom-s", type=float, default=0.8)
    ap.add_argument("--bin-ms", type=float, default=5.0)
    ap.add_argument("--decode", default=None, help="decode nodes (default: the serve_<node>.log ones)")
    a = ap.parse_args()
    global BIN
    BIN = a.bin_ms / 1e3
    rd = a.run_dir
    dec = a.decode.split() if a.decode else sorted(re.sub(r"^serve_|\.log$", "", os.path.basename(f))
                                                  for f in glob.glob(os.path.join(rd, "serve_*.log")) if "try" not in f)
    nm = {re.sub(r"^nicmon_|\.csv\.gz$", "", os.path.basename(f)): load_nicmon(f)
          for f in glob.glob(os.path.join(rd, "nicmon_*.csv.gz"))}
    src = [n for n in nm if n not in dec]
    ws = windows(rd)
    lv = live(rd)
    base = [steps(lv, w[1], w[2]) for w in ws if w[0].startswith("base")]
    b0 = float(np.nanmean(base)) if base else float("nan")
    print(f"run {os.path.basename(os.path.normpath(rd))}: decode {dec}, IO source {src}, base windows' step "
          f"{' / '.join(f'{x:.1f}' for x in base)} ms")
    hdr = (f"| window | step (ms) | IO off around it (ms) | change | EP TX / NIC mean (Gb/s) | EP {a.bin_ms:g} ms p50 / p99 "
           f"| decode RX {a.bin_ms:g} ms p99 / max | KV transfers | KV ms p50 / p99 | KV latency ms p50 / p99 | KV GB/s / NIC "
           "| retx pkts/s (source) | seq err/s (decode) | CNP/s (decode) |")
    print(hdr)
    print("|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|")
    for wi, (w, t0, t1, _, _) in enumerate(ws):
        s = steps(lv, t0, t1)
        lb = local_base(lv, ws, wi)
        txs, rxs, cnp, seq = [], [], 0.0, 0.0
        for n in dec:
            if n not in nm:
                continue
            S, st, sv = nm[n]
            tx, rx, _ = bins(S, t0, t1)
            if tx is not None:
                txs.append(tx); rxs.append(rx)
            cnp += slow_rate(st, sv, t0, t1, 3)
            seq += slow_rate(st, sv, t0, t1, 5) + slow_rate(st, sv, t0, t1, 6)
        retx = sum(slow_rate(nm[n][1], nm[n][2], t0, t1, 0) for n in nm)
        tx = np.concatenate(txs) if txs else np.zeros((1, 8))
        rx = np.concatenate(rxs) if rxs else np.zeros((1, 8))
        io = io_records(rd, w) if not w.startswith("base") else dict(n=0, dur50=np.nan, dur99=np.nan, lat50=np.nan,
                                                                       lat99=np.nan, rate50=np.nan)
        print(f"| {w} | {s:.1f} | {lb:.1f} | {(s / lb - 1) * 100:+.1f}% | {tx.mean():.1f} | {np.percentile(tx, 50):.0f} / "
              f"{np.percentile(tx, 99):.0f} | {np.percentile(rx, 99):.0f} / {rx.max():.0f} | {io['n']} | "
              f"{io['dur50']:.1f} / {io['dur99']:.1f} | {io['lat50']:.1f} / {io['lat99']:.1f} | {io['rate50']:.1f} | "
              f"{retx:.0f} | {seq:.0f} | {cnp:.0f} |")
        if a.plots and dec and dec[0] in nm:
            os.makedirs(a.plots, exist_ok=True)
            S = nm[dec[0]][0]
            tx, rx, t = bins(S, t0, t1)
            if tx is not None:
                plot(os.path.join(a.plots, f"{w}_{dec[0]}_overview.png"), f"{w} {dec[0]}", t, tx, rx)
                z0 = t0 + 30
                tx, rx, t = bins(S, z0, z0 + a.zoom_s)
                plot(os.path.join(a.plots, f"{w}_{dec[0]}_zoom.png"), f"{w} {dec[0]} ({a.zoom_s:g} s)", t, tx, rx)


if __name__ == "__main__":
    main()
