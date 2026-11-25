#!/usr/bin/env python3
"""epio_summary.py: one row per window of a run, from what it pushed, back into VictoriaMetrics as epio_summary (the
dashboard's summary table) and as markdown on stdout.

  python3 tools/epio_summary.py results/e2e/<run> --vm http://127.0.0.1:28528 --experiment yutongwu-<run>

Windows from <run>/events.tsv; each is measured while its IO runs (<w>_io_on .. <w>_io_end; base: 20 s after its start
to its end), against the gap before it (the previous window's end + 15 s to its start: the decode's step drifts as the
contexts grow, so a window is compared with the stretch right before it). Columns:
  step: the median of the per-second step p50 of every DP rank (e2e/loadgen.py), and its change against the gap before
  dispatch / combine: the window's profiler capture (tools/ep_push.py), mean over every call; dispatch against base's
  decode CNP/s, decode RX per NIC, retransmitted packets/s on every node: ainic-netmon, over the stretch
  KV: every transfer that ended in the stretch (mb/io_inject.py --push): time p50 / p99, arrival-to-done p99, GiB/s
"""
import argparse
import json
import urllib.parse
import urllib.request

import numpy as np

COLS = ["01 每步 ms", "02 每步 vs 前面空档 %", "03 dispatch µs", "04 dispatch vs base %", "05 combine µs",
        "06 decode CNP/s", "07 decode 每张卡收 Gb/s", "08 KV 每次 p50 ms", "09 KV 每次 p99 ms", "10 KV 到达到完成 p99 ms",
        "11 KV GiB/s", "12 重传 包/s", "13 TTFT p50 ms", "14 TTFT p99 ms", "15 decode 等 KV p50 ms",
        "16 decode 等 KV p99 ms"]


def get(vm, path, **params):
    return json.load(urllib.request.urlopen(f"{vm}{path}?{urllib.parse.urlencode(params)}", timeout=60))


def export(vm, match, a, b):
    raw = urllib.request.urlopen(f"{vm}/api/v1/export?" + urllib.parse.urlencode(
        {"match[]": match, "start": f"{a:.3f}", "end": f"{b:.3f}"}), timeout=120).read().decode()
    return [json.loads(l) for l in raw.splitlines() if l.strip()]


def scalar(vm, q, t):
    r = get(vm, "/api/v1/query", query=q, time=f"{t:.3f}")["data"]["result"]
    return float(r[0]["value"][1]) if r else float("nan")


def windows(run_dir):
    ev = [l.rstrip("\n").split("\t") for l in open(f"{run_dir}/events.tsv") if l.count("\t") >= 2]
    ws, cur = [], {}
    for t, name, arg in ev:
        t = float(t)
        if arg == "start" and not name.startswith("prof_"):
            cur[name] = {"name": name, "t0": t}
        elif arg == "end" and name in cur:
            w = cur.pop(name)
            w["t1"] = t
            ws.append(w)
        elif name.endswith("_io_on") and name[:-6] in cur:
            cur[name[:-6]]["a"] = t
        elif name.endswith("_io_end") and name[:-7] in cur:
            cur[name[:-7]]["b"] = t
    for w in ws:
        w.setdefault("a", w["t0"] + 20)
        w.setdefault("b", w["t1"])
    return ws


def step_med(vm, E, a, b):
    s = export(vm, f'epio_decode_step_ms{{{E},q="p50"}}', a, b)
    per = [np.median(x["values"]) for x in s if x["values"]]
    return float(np.mean(per)) if per else float("nan")


def netmon_rate(vm, E, counter, hosts, a, b):
    d = b - a
    q = (f'sum(sum_over_time(netmon_increase{{{E},host=~"{hosts}",source="rdma",counter="{counter}"}}[{d:.0f}s]))')
    return scalar(vm, q, b) / d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--vm", default="http://127.0.0.1:28528")
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    vm, E = a.vm.rstrip("/"), f'experiment="{a.experiment}"'
    nodes = get(vm, "/api/v1/series", **{"match[]": f"epio_node{{{E}}}", "start": "-30d"})["data"]
    decode = "|".join(sorted(n["host"] for n in nodes if n.get("role") == "decode")) or ".*"
    nd = max(1, len(decode.split("|"))) * 8
    ws = windows(a.run_dir)
    ep = {}
    for x in get(vm, "/api/v1/query", query=f'last_over_time(epio_ep_us{{{E},stat="mean"}}[30d])')["data"]["result"]:
        ep[(x["metric"]["window"], x["metric"]["kernel"])] = float(x["value"][1])
    rows, prev_end = [], None
    for w in ws:
        A, B = w["a"], w["b"]
        st = step_med(vm, E, A, B)
        lb = step_med(vm, E, prev_end + 15, w["t0"]) if prev_end and w["t0"] - prev_end > 30 else float("nan")
        prev_end = w["t1"]
        kv = export(vm, f'epio_kv_ms{{{E},window="{w["name"]}"}}', A, B + 30)
        tr = np.concatenate([x["values"] for x in kv if x["metric"].get("kind") == "transfer"] or [np.zeros(0)])
        la = np.concatenate([x["values"] for x in kv if x["metric"].get("kind") == "latency"] or [np.zeros(0)])
        mb = export(vm, f'epio_kv_mb{{{E},window="{w["name"]}"}}', A, B)
        kv_mb = sum(sum(x["values"]) for x in mb)
        pct = lambda v, q: float(np.percentile(v, q)) if len(v) else float("nan")  # noqa: E731
        disp, base_disp = ep.get((w["name"], "dispatch"), float("nan")), ep.get(("base", "dispatch"), float("nan"))
        pd = {m: np.concatenate([x["values"] for x in export(vm, f"{m}{{{E}}}", A, B)] or [np.zeros(0)])
              for m in ("epio_pd_ttft_ms", "epio_pd_decode_wait_ms")}
        r = [st, (st / lb - 1) * 100 if lb == lb else float("nan"), disp, (disp / base_disp - 1) * 100,
             ep.get((w["name"], "combine"), float("nan")),
             netmon_rate(vm, E, "rx_rdma_cnp_pkts", decode, A, B),
             netmon_rate(vm, E, "rx_rdma_ucast_bytes", decode, A, B) * 8 / 1e9 / nd,
             pct(tr, 50), pct(tr, 99), pct(la, 99), kv_mb / 1024 / (B - A),
             netmon_rate(vm, E, "tx_rdma_retx_pkts", ".*", A, B),
             pct(pd["epio_pd_ttft_ms"], 50), pct(pd["epio_pd_ttft_ms"], 99),
             pct(pd["epio_pd_decode_wait_ms"], 50), pct(pd["epio_pd_decode_wait_ms"], 99)]
        rows.append((w, r))
    print("| 窗口 | " + " | ".join(c[3:] for c in COLS) + " |")
    print("|---" * (len(COLS) + 1) + "|")
    for w, r in rows:
        print(f"| {w['name']} | " + " | ".join("-" if v != v else f"{v:.1f}" for v in r) + " |")
    if a.no_push:
        return
    lines = []
    for w, r in rows:
        for c, v in zip(COLS, r):
            if v == v:
                lines.append(f'epio_summary{{{E},window="{w["name"]}",metric="{c}"}} {v:.6g} {int(w["b"] * 1000)}')
    urllib.request.urlopen(urllib.request.Request(f"{vm}/api/v1/import/prometheus", data=("\n".join(lines) + "\n").encode(),
                                                  method="POST"), timeout=60).read()
    print(f"pushed {len(lines)} summary values")


if __name__ == "__main__":
    main()
