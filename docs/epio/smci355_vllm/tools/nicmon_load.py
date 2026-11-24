"""Reading tools/nicmon.py traces: per-NIC read times, byte counters, the slow counters, 10 ms rates."""
import gzip

import numpy as np

SLOW = ("tx_rdma_retx_pkts", "tx_rdma_retx_bytes", "tx_rdma_ack_timeout", "rx_rdma_cnp_pkts", "rx_rdma_ecn_pkts",
        "req_rx_pkt_seq_err", "resp_rx_outouf_seq", "rx_rdma_ucast_pkts")
NDEV = 8


def load(path):
    """(times [n, 8] of each NIC's read, byte counters [n, 16] tx/rx per NIC, slow row times [m], slow counters [m, 64]).
    Traces without per-NIC read times (rows of 18 fields) spread each sweep's NICs evenly up to the next row."""
    rows, offs, s_t, s_v = [], [], [], []
    with gzip.open(path, "rt") as fh:
        try:
            for line in fh:
                p = line.rstrip("\n").split(",")
                if p[0] == "f" and len(p) in (2 + 2 * NDEV, 2 + 3 * NDEV):
                    rows.append([float(p[1])] + [int(x) for x in p[2:2 + 2 * NDEV]])
                    offs.append([int(x) for x in p[2 + 2 * NDEV:]] or None)
                elif p[0] == "s" and len(p) == 2 + NDEV * len(SLOW):
                    s_t.append(float(p[1])); s_v.append([int(x) for x in p[2:]])
        except (EOFError, OSError):  # a trace still being written, or whose writer was killed
            pass
    if not rows:
        return np.zeros((0, NDEV)), np.zeros((0, 2 * NDEV)), np.array(s_t), np.array(s_v, dtype=np.float64)
    a = np.array(rows, dtype=np.float64)
    t, v = a[:, 0], a[:, 1:]
    tn = np.repeat(t[:, None], NDEV, 1)
    has = np.array([o is not None for o in offs])
    if has.any():
        tn[has] += np.array([o for o in offs if o is not None], dtype=np.float64) / 1e6
    if (~has).any():
        nxt = np.append(t[1:], t[-1] + 0.012)
        frac = (np.arange(NDEV) + 1) / NDEV
        tn[~has] += (nxt - t)[~has, None] * frac[None, :]
    return tn, v, np.array(s_t), np.array(s_v, dtype=np.float64)


def backfill(r, cap):
    """whatever a bin has above cap moves back into the bins before it, each filled up to cap (a safety net for a
    refresh that still carries more than the link could have moved since the previous one)"""
    r = r.copy()
    for i in np.where(r > cap)[0]:
        ex, r[i], k = r[i] - cap, cap, i - 1
        while ex > 0 and k >= 0:
            add = min(ex, cap - r[k]) if r[k] < cap else 0.0
            r[k] += add; ex -= add; k -= 1
    return r


def rates(tn, v, grid, cap_gbps=420.0):
    """Gb/s on the bins between the edges in grid: (tx [n, 8], rx [n, 8])"""
    tx, rx = np.zeros((len(grid) - 1, NDEV)), np.zeros((len(grid) - 1, NDEV))
    if len(tn) < 2:
        return tx, rx
    dt = np.diff(grid)
    for i in range(NDEV):
        o = np.argsort(tn[:, i], kind="stable")
        for k, out in ((0, tx), (1, rx)):
            c = np.interp(grid, tn[o, i], v[o, 2 * i + k])
            out[:, i] = backfill(np.diff(c) / dt * 8 / 1e9, cap_gbps)
    return tx, rx
