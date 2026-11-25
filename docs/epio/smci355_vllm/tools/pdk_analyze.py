#!/usr/bin/env python3
"""pdk_analyze.py: the real-KV P/D runs (tools/launch2.sh pdk* phases) side by side, over their natural stretch.

  python3 tools/pdk_analyze.py results/e2e/pdkr104_<job> results/e2e/pdkr0_<job> ...

Per run: the config (KV mode, traffic class, mori io settings from logs/env_<run>.txt), the decode step (the proxy's
median gap between streamed chunks, 10 s records), TTFT p50 / p99 and, in READ mode, the decode leg's part of it (TTFT
minus the prefill leg: the KV read plus the first step), finished requests / s, the MoRI EP dispatch / combine kernels
(the profiler capture in the middle of the stretch, e2e/e1_analyze.py's summary.json), and from ainic-netmon: per
decode NIC the RX (KV + EP) and TX (EP) means, the p99 of the per-interval RX, CNPs / s at the decode nodes,
retransmitted packets / s at every node, sequence errors / s at the decode nodes.
"""
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netmon_read as nr  # noqa: E402

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def events(rd):
    ev = {}
    for line in open(os.path.join(rd, "events.tsv")):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3:
            ev.setdefault((p[1], p[2]), float(p[0]))
    return ev


def env_of(run):
    f = os.path.join(W, "logs", f"env_{run}.txt")
    txt = open(f).read() if os.path.exists(f) else ""
    get = lambda k, d="": (re.search(rf"\b{k}=\"?([^\"\n]*)\"?", txt) or [None, d])[1]  # noqa: E731
    return get("KVMODE", "read"), get("KVTC", "104"), get("KV_ENV", "")


def decode_nodes(rd):
    return sorted(re.sub(r"^serve_|\.log$", "", f) for f in os.listdir(rd) if re.match(r"^serve_[^.]+\.log$", f))


def main():
    rows = []
    for rd in sys.argv[1:]:
        run = os.path.basename(os.path.normpath(rd))
        ev = events(rd)
        t0, t1 = ev.get(("nat", "start")), ev.get(("nat", "end"))
        if not t0 or not t1:
            print(f"{run}: no natural stretch", file=sys.stderr)
            continue
        mode, tc, kv = env_of(run)
        live = [json.loads(x) for x in open(os.path.join(rd, "load", "live.jsonl")) if x.strip()]
        gaps = [r["gap_med_ms"] for r in live if t0 + 10 <= r["t"] <= t1 and r.get("gap_med_ms")]
        reqs = [json.loads(x) for x in open(os.path.join(rd, "load", "requests.jsonl")) if x.strip()]
        done = [r for r in reqs if r.get("done") and r.get("t_first") and t0 <= r["t0"] <= t1]
        ttft = np.array([r["t_first"] - r["t0"] for r in done]) * 1e3
        dleg = np.array([r["t_first"] - r["t0"] - r["t_pf"] for r in done if r.get("t_pf") is not None]) * 1e3
        fin = sum(1 for r in reqs if r.get("done") and t0 <= r.get("t_end", 0) <= t1) / (t1 - t0)
        cap = {}
        sj = os.path.join(rd, "summary.json")
        if os.path.exists(sj):
            cap = json.load(open(sj)).get("captures", {})
        nat = cap.get("nat_1", [])
        disp = np.mean([c["dispatch_mean"] for c in nat]) if nat else np.nan
        comb = np.mean([c["combine_mean"] for c in nat]) if nat else np.nan
        nm = nr.runs(rd)
        dec = decode_nodes(rd)
        rx, tx, rxp, cnp, seq = [], [], [], 0.0, 0.0
        for n in dec:
            if n not in nm:
                continue
            for _, _, r in nr.gbps(nm[n], nr.RX, t0, t1):
                rxp.append(r)
            rx.append(nr.mean_rate(nm[n], nr.RX, t0, t1) * 8 / 1e9 / 8)
            tx.append(nr.mean_rate(nm[n], nr.TX, t0, t1) * 8 / 1e9 / 8)
            cnp += nr.mean_rate(nm[n], nr.CNP, t0, t1)
            seq += sum(nr.mean_rate(nm[n], c, t0, t1) for c in nr.SEQERR)
        retx = sum(nr.mean_rate(r, nr.RETX, t0, t1) for r in nm.values())
        rxp = np.concatenate(rxp) if rxp else np.zeros(1)
        pct = lambda a, q: float(np.percentile(a, q)) if len(a) else float("nan")  # noqa: E731
        rows.append((run, mode, tc, kv.replace("MORI_IO_", "") or "-", float(np.median(gaps)) if gaps else np.nan,
                     pct(ttft, 50), pct(ttft, 99), pct(dleg, 50), pct(dleg, 99), fin, disp, comb,
                     float(np.mean(rx)) if rx else np.nan, float(np.mean(tx)) if tx else np.nan, pct(rxp, 99), cnp,
                     retx, seq))
    hdr = ("| run | KV mode | KV TC | mori io | decode step (ms) | TTFT p50 / p99 (ms) | decode leg p50 / p99 (ms) "
           "| finished req/s | EP dispatch / combine (µs) | decode RX / TX per NIC (Gb/s) | RX per interval p99 (Gb/s) "
           "| CNP/s (decode) | retx pkts/s | seq err/s (decode) |")
    print(hdr)
    print("|" + "|".join(["---"] * (hdr.count("|") - 1)) + "|")
    for r in rows:
        print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]:.1f} | {r[5]:.0f} / {r[6]:.0f} | {r[7]:.0f} / {r[8]:.0f} | "
              f"{r[9]:.1f} | {r[10]:.0f} / {r[11]:.0f} | {r[12]:.1f} / {r[13]:.1f} | {r[14]:.0f} | {r[15]:.0f} | "
              f"{r[16]:.0f} | {r[17]:.0f} |")


if __name__ == "__main__":
    main()
