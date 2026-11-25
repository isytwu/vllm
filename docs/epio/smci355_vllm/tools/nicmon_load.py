"""Reading tools/nicmon.py traces: each NIC's samples (read time, tx bytes, rx bytes), the slow counters, rates."""
import gzip

import numpy as np

SLOW = ("tx_rdma_retx_pkts", "tx_rdma_retx_bytes", "tx_rdma_ack_timeout", "rx_rdma_cnp_pkts", "rx_rdma_ecn_pkts",
        "req_rx_pkt_seq_err", "resp_rx_outouf_seq", "rx_rdma_ucast_pkts")
NDEV = 8


def load(path):
    """([(read times, tx bytes, rx bytes) of each NIC], slow row times [m], slow counters [m, 64]).
    Per-NIC rows (n,...) as written; older sweep rows (f,...) give each NIC its read time from the row's offsets, or,
    without them, spread each sweep's NICs evenly up to the next row."""
    per = [[] for _ in range(NDEV)]
    rows, offs, s_t, s_v = [], [], [], []
    with gzip.open(path, "rt") as fh:
        try:
            for line in fh:
                p = line.rstrip("\n").split(",")
                if p[0] == "n" and len(p) == 5:
                    per[int(p[1])].append((float(p[2]), int(p[3]), int(p[4])))
                elif p[0] == "f" and len(p) in (2 + 2 * NDEV, 2 + 3 * NDEV):
                    rows.append([float(p[1])] + [int(x) for x in p[2:2 + 2 * NDEV]])
                    offs.append([int(x) for x in p[2 + 2 * NDEV:]] or None)
                elif p[0] == "s" and len(p) == 2 + NDEV * len(SLOW):
                    s_t.append(float(p[1])); s_v.append([int(x) for x in p[2:]])
        except (EOFError, OSError):  # a trace still being written, or whose writer was killed
            pass
    if rows:
        a = np.array(rows, dtype=np.float64)
        t = a[:, 0]
        tn = np.repeat(t[:, None], NDEV, 1)
        has = np.array([o is not None for o in offs])
        if has.any():
            tn[has] += np.array([o for o in offs if o is not None], dtype=np.float64) / 1e6
        if (~has).any():
            nxt = np.append(t[1:], t[-1] + 0.012)
            tn[~has] += (nxt - t)[~has, None] * ((np.arange(NDEV) + 1) / NDEV)[None, :]
        for i in range(NDEV):
            per[i] += list(zip(tn[:, i], a[:, 1 + 2 * i], a[:, 2 + 2 * i]))
    series = []
    for s in per:
        s = np.array(sorted(s), dtype=np.float64).reshape(-1, 3)
        series.append((s[:, 0], s[:, 1], s[:, 2]))
    return series, np.array(s_t), np.array(s_v, dtype=np.float64)


def span(series):
    """first and last sample time over the NICs"""
    ts = [s[0] for s in series if len(s[0])]
    return (min(t[0] for t in ts), max(t[-1] for t in ts)) if ts else (np.nan, np.nan)


def backfill(r, cap):
    """whatever a bin has above cap moves back into the bins before it, each filled up to cap (a safety net for a
    sample that still carries more than the link could have moved since the previous one)"""
    r = r.copy()
    for i in np.where(r > cap)[0]:
        ex, r[i], k = r[i] - cap, cap, i - 1
        while ex > 0 and k >= 0:
            add = min(ex, cap - r[k]) if r[k] < cap else 0.0
            r[k] += add; ex -= add; k -= 1
    return r


def rates(series, grid, cap_gbps=420.0):
    """Gb/s on the bins between the edges in grid: (tx [n, 8], rx [n, 8])"""
    tx, rx = np.zeros((len(grid) - 1, NDEV)), np.zeros((len(grid) - 1, NDEV))
    dt = np.diff(grid)
    for i, (t, txb, rxb) in enumerate(series):
        if len(t) < 2:
            continue
        for cum, out in ((txb, tx), (rxb, rx)):
            out[:, i] = backfill(np.diff(np.interp(grid, t, cum)) / dt * 8 / 1e9, cap_gbps)
    return tx, rx
