#!/usr/bin/env python3
"""grafana_dash.py: the nicmon dashboard (monitor/grafana-conf/dashboards/nicmon.json) for Grafana's file provisioning.

  python3 tools/grafana_dash.py [results/e2e/<run> [<window>]]

The panels mirror Moonshot's decode-node screenshots ("IB std port_rcv_data / port_xmit_data", per NIC, 10 ms):
RX and TX of every NIC of the chosen node, every node's totals, CNP / ECN / retransmissions / sequence errors, the IO
windows and the decode step. With a run (and a window, default the first MK window) the default time range is the
first 20 s of that window's IO.
"""
import json
import os
import sys

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = {"type": "prometheus", "uid": "vm"}


def ts_panel(title, expr, legend, x, y, w, h, unit="suffix: Gb/s", desc=""):
    return {"type": "timeseries", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "interval": "10ms", "maxDataPoints": 4000,
            "targets": [{"refId": "A", "datasource": DS, "expr": expr, "legendFormat": legend, "range": True}],
            "fieldConfig": {"defaults": {"unit": unit, "decimals": 0, "min": 0,
                                         "custom": {"lineWidth": 1, "fillOpacity": 0, "showPoints": "never",
                                                    "spanNulls": False}}, "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["mean", "max"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def var(name, query, multi=False):
    return {"name": name, "type": "query", "datasource": DS, "refresh": 2, "sort": 1,
            "query": {"query": query, "refId": name}, "definition": query,
            "multi": multi, "includeAll": multi, **({"allValue": ".*"} if multi else {})}


def time_range(run_dir, window):
    ev = os.path.join(run_dir, "events.tsv")
    for line in open(ev):
        p = line.rstrip("\n").split("\t")
        if len(p) > 1 and (p[1] == f"{window}_io_on" or (window is None and p[1].startswith("MK") and p[1].endswith("_io_on"))):
            t = float(p[0])
            return int((t - 25) * 1000), int((t - 5) * 1000)   # io_on is logged 20 s after the IO starts
    return None


def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else None
    window = sys.argv[2] if len(sys.argv) > 2 else None
    sel = 'run="$run",node="$node",nic=~"$nic"'
    panels = [
        ts_panel("$node port_rcv_data（每张网卡接收，10 ms）", f"max_over_time(nic_rx_gbps{{{sel}}}[$__interval])",
                 "{{nic}}", 0, 0, 12, 10, desc="decode 节点：EP（另一台 decode 发来）+ KV / 注入的 IO"),
        ts_panel("$node port_xmit_data（每张网卡发送，10 ms）", f"max_over_time(nic_tx_gbps{{{sel}}}[$__interval])",
                 "{{nic}}", 12, 0, 12, 10, desc="decode 节点只发 EP 和 READ 请求；C 节点发的是注入的 IO"),
        ts_panel("每台机器接收合计（8 张网卡）", 'sum by (node) (nic_rx_gbps{run="$run"})', "{{node}}", 0, 10, 12, 8),
        ts_panel("每台机器发送合计（8 张网卡）", 'sum by (node) (nic_tx_gbps{run="$run"})', "{{node}}", 12, 10, 12, 8),
        ts_panel("$node 收到的 CNP（每秒，每张网卡）", f'nic_cnp_pps{{{sel}}}', "{{nic}}", 0, 18, 8, 8, unit="short",
                 desc="decode 收到 CNP = 它发出的 EP 包在交换机里排队、被打了 ECN 标记"),
        ts_panel("重传包（每秒，每台）", 'sum by (node) (nic_retx_pps{run="$run"})', "{{node}}", 8, 18, 8, 8,
                 unit="short", desc="TC 0 有损：丢包后发送端重传"),
        ts_panel("序号错（每秒，每台）", 'sum by (node) (nic_seqerr_pps{run="$run"})', "{{node}}", 16, 18, 8, 8,
                 unit="short", desc="接收端看到的乱序 / 缺包"),
        {"type": "state-timeline", "title": "注入窗口", "datasource": DS, "gridPos": {"x": 0, "y": 26, "w": 16, "h": 7},
         "targets": [{"refId": "A", "datasource": DS, "expr": 'max by (window) (window_on{run="$run"})',
                      "legendFormat": "{{window}}", "range": True}],
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": [
             {"color": "transparent", "value": None}, {"color": "orange", "value": 0.5}]}}, "overrides": []},
         "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8}},
        ts_panel("decode 每步（ms，客户端 10 秒中位数）", 'step_ms{run="$run"}', "step", 16, 26, 8, 7, unit="ms"),
    ]
    dash = {"uid": "nicmon", "title": "nicmon：每张网卡 10 ms 流量（smci355）", "timezone": "browser", "schemaVersion": 39,
            "editable": True, "refresh": "", "panels": panels,
            "templating": {"list": [var("run", "label_values(nic_rx_gbps, run)"),
                                    var("node", 'label_values(nic_rx_gbps{run="$run"}, node)'),
                                    var("nic", 'label_values(nic_rx_gbps{run="$run",node="$node"}, nic)', True)]},
            "time": {"from": "now-6h", "to": "now"}}
    if run_dir:
        tr = time_range(run_dir, window)
        if tr:
            dash["time"] = {"from": str(tr[0]), "to": str(tr[1])}
        run = os.path.basename(os.path.normpath(run_dir))
        dash["templating"]["list"][0]["current"] = {"text": run, "value": run}
    out = os.path.join(W, "monitor", "grafana-conf", "dashboards", "nicmon.json")
    json.dump(dash, open(out, "w"), ensure_ascii=False, indent=1)
    print(out, dash["time"])


if __name__ == "__main__":
    main()
