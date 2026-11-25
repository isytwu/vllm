"""Reading ainic-netmon recordings (tools/netmon_bin; <run>/netmon/<host>) for the analysis and the figures.

Every rate is one sampling interval of one NIC, computed by the recorder's own report library
(tools/netmon_report/ainic_netmon_report.py: sample time = request start + 0.7 x its round trip, slow requests skipped,
counter resets, wall-clock alignment), so the numbers here match ainic-netmon-report and the netmon dashboards.
"""
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "netmon_report"))
import ainic_netmon_report as R  # noqa: E402

TX, RX = R.RDMA_TX, R.RDMA_RX
CNP, ECN, RETX = "rx_rdma_cnp_pkts", "rx_rdma_ecn_pkts", "tx_rdma_retx_pkts"
SEQERR = ("req_rx_pkt_seq_err", "resp_rx_outouf_seq")


def runs(run_dir):
    """{host: ainic_netmon_report.Run} of <run_dir>/netmon/<host> (empty when the run has no recordings)"""
    out = {}
    for d in sorted(glob.glob(os.path.join(run_dir, "netmon", "*"))):
        if not os.path.isdir(d):
            continue
        try:
            out[os.path.basename(d)] = R.Run(d)
        except (SystemExit, OSError, ValueError):
            pass
    return out


def nics(run):
    """[(ionic index, rdma stream)] in ionic order (the netdev names do not follow it)"""
    out = []
    for s in run.streams:
        ib = next((n.get("ibdev") for n in run.meta.get("nics", []) if n["netdev"] == s.netdev), None)
        if s.source == "rdma" and ib and ib.startswith("ionic_"):
            out.append((int(ib.split("_")[1]), s))
    return sorted(out, key=lambda x: x[0])


def nic_rates(run, counter, t0=None, t1=None, scale=1.0):
    """[(ionic index, interval end times s, rate per s x scale)] per NIC: valid intervals, ending in [t0, t1]"""
    out = []
    for i, s in nics(run):
        r = R.Rates(s, counter)
        m = r.valid.copy()
        if t0 is not None:
            m &= r.w_end >= t0 * 1e9
        if t1 is not None:
            m &= r.w_end <= t1 * 1e9
        out.append((i, r.w_end[m] / 1e9, r.rate[m] * scale))
    return out


def gbps(run, counter, t0=None, t1=None):
    """nic_rates of a byte counter in Gb/s"""
    return nic_rates(run, counter, t0, t1, scale=8 / 1e9)


def gbps_pair(run, t0=None, t1=None):
    """[(ionic index, interval end times s, TX Gb/s, RX Gb/s)]: the intervals valid for both byte counters"""
    out = []
    for i, s in nics(run):
        a, b = R.Rates(s, TX), R.Rates(s, RX)
        m = a.valid & b.valid
        if t0 is not None:
            m &= a.w_end >= t0 * 1e9
        if t1 is not None:
            m &= a.w_end <= t1 * 1e9
        out.append((i, a.w_end[m] / 1e9, a.rate[m] * 8 / 1e9, b.rate[m] * 8 / 1e9))
    return out


def mean_rate(run, counter, t0, t1):
    """average per second over [t0, t1] summed over the NICs (time-weighted: increase / covered time)"""
    tot = 0.0
    for i, s in nics(run):
        r = R.Rates(s, counter)
        m = r.valid & (r.w_start >= t0 * 1e9) & (r.w_end <= t1 * 1e9)
        if m.any():
            tot += float(r.dv[m].sum() / (r.dt[m].sum() / 1e9))
    return tot
