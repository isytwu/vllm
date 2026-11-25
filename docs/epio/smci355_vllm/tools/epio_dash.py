#!/usr/bin/env python3
"""epio_dash.py: the experiment dashboard (EP x KV on smci355) for our Grafana, from the series the runs push.

  python3 tools/epio_dash.py [--out monitor/grafana-conf/dashboards/epio.json]

One experiment at a time ($experiment: a run's yutongwu-<run>), its decode nodes ($decode) and KV sources ($source)
from epio_node. What each row shows and where it comes from:
  windows          epio_window / epio_io (e2e/windows.sh ev): which window runs, and when its IO runs
  decode           epio_decode_* (e2e/loadgen.py --push): step time p50 / p99 per DP rank every second, sequence-steps
                   per second, open streams
  EP               epio_ep_us (tools/ep_push.py): MoRI dispatch / combine per profiler capture (one per window)
  KV               epio_kv_ms / epio_kv_mb (mb/io_inject.py --push): every transfer's time and latency; p50 / p99 over a
                   sliding 30 s, transfers and GiB per second
  NICs             netmon_* (ainic-netmon, 2 ms): RX / TX per NIC (interval average; the highest 2 ms value), the KV
                   source's TX, CNPs and ECN marks, retransmissions, ACK timeouts, PFC pauses
  summary          epio_summary (tools/epio_summary.py, after the run): one row per window
"""
import argparse
import json
import os

DS = {"type": "prometheus", "uid": "vm"}
E = 'experiment="$experiment"'


def tgt(expr, legend="", ref="A", instant=False, fmt=None):
    t = {"refId": ref, "expr": expr, "legendFormat": legend, "range": not instant, "instant": instant, "datasource": DS}
    if fmt:
        t["format"] = fmt
    return t


def ts_panel(title, targets, x, y, w=12, h=8, unit="none", desc="", points=False, overrides=None, interval=None,
             stack=False, min0=True):
    custom = {"drawStyle": "points" if points else "line", "lineWidth": 1, "fillOpacity": 0, "spanNulls": False,
              "showPoints": "always" if points else "never", "pointSize": 9 if points else 3,
              "stacking": {"mode": "normal" if stack else "none"}}
    p = {"type": "timeseries", "title": title, "description": desc, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
         "fieldConfig": {"defaults": {"unit": unit, "custom": custom, **({"min": 0} if min0 else {})},
                         "overrides": overrides or []},
         "options": {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    if interval:
        p["interval"] = interval
        p["maxDataPoints"] = 2000
    return p


def right_axis(ref, unit="none"):
    return {"matcher": {"id": "byFrameRefID", "options": ref},
            "properties": [{"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": unit}]}


def row(title, y):
    return {"type": "row", "title": title, "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def nic_avg(counter, hosts):
    """each NIC's average rate over the panel's interval (ainic-netmon's own expression), per second"""
    sel = f'{E},host=~"{hosts}",source="rdma",counter="{counter}"'
    return (f"(sum without (resolution) (sum_over_time(netmon_increase{{{sel}}}[$__interval])) / "
            f"sum without (resolution) (sum_over_time(netmon_interval_seconds{{{sel}}}[$__interval])))")


def nic_peak(counter, hosts):
    sel = f'{E},host=~"{hosts}",source="rdma",counter="{counter}"'
    return f"max without (resolution) (max_over_time(netmon_rate{{{sel}}}[$__interval]))"


def kv_q(q, kind):
    return (f'histogram_quantile({q}, sum(histogram_over_time(epio_kv_ms{{{E},kind="{kind}"}}[30s])) by (vmrange))')


def build():
    P = []
    y = 0
    P.append(row("窗口与 decode 性能", y)); y += 1
    P.append({"type": "state-timeline", "title": "窗口：正在跑哪个窗口（上）/ 它的 IO 在跑（下）", "datasource": DS,
              "description": "e2e/windows.sh 的事件：窗口开始到结束；IO 从 <窗口>_io_on 到 <窗口>_io_end",
              "gridPos": {"x": 0, "y": y, "w": 24, "h": 5},
              "targets": [tgt(f'max by (name) (epio_window{{{E},name!~"prof_.*"}})', "{{name}}", "A"),
                          tgt(f"max by (name) (epio_io{{{E}}})", "{{name}} 的 IO", "B")],
              "fieldConfig": {"defaults": {"custom": {"fillOpacity": 85, "lineWidth": 0},
                                           "mappings": [{"type": "value", "options": {
                                               "1": {"text": "运行", "color": "green", "index": 0},
                                               "0": {"text": " ", "color": "transparent", "index": 1}}}]},
                              "overrides": [{"matcher": {"id": "byFrameRefID", "options": "B"},
                                             "properties": [{"id": "mappings", "value": [{"type": "value", "options": {
                                                 "1": {"text": "IO", "color": "orange", "index": 0},
                                                 "0": {"text": " ", "color": "transparent", "index": 1}}}]}]}]},
              "options": {"mergeValues": True, "showValue": "never", "rowHeight": 0.85, "alignValue": "left",
                          "legend": {"showLegend": False}}})
    y += 5
    P.append(ts_panel("decode 每步时长：每个 DP rank，每秒的 p50 / p99 取 30 秒中位数（ms）",
                      [tgt(f'quantile_over_time(0.5, epio_decode_step_ms{{{E},q=~"p50|p99"}}[30s])', "{{server}} {{q}}")],
                      0, y, unit="ms", min0=False,
                      desc="负载程序（e2e/loadgen.py / pd_proxy.py）每秒算一次：同一条流相邻两次收到数据的间隔 = 一步。"
                           "取 30 秒中位数，去掉 profiler 采样那几秒（单步约 2.5 s）的尖峰"))
    P.append(ts_panel("decode 吞吐：每秒的序列步数（左）/ 在跑的序列（右）",
                      [tgt(f"sum by (server) (epio_decode_chunks_per_s{{{E}}})", "{{server}} 序列步/秒", "A"),
                       tgt(f"sum by (server) (epio_decode_streams{{{E}}})", "{{server}} 在跑的序列", "B")],
                      12, y, overrides=[right_axis("B")]))
    y += 8
    P.append(ts_panel("EP：每次 profiler 采样的 MoRI dispatch / combine 耗时（所有 rank，µs）",
                      [tgt(f'epio_ep_us{{{E},stat=~"mean|p90"}}', "{{kernel}} {{stat}}（{{window}}）")], 0, y,
                      unit="µs", points=True,
                      desc="每个窗口中间采约 8 秒（tools/ep_push.py 解析 torch profiler 的 trace），所以是一个窗口一个点"))
    P.append(ts_panel("EP：每个 rank 的 dispatch 均值（µs，看有没有个别 rank 慢）",
                      [tgt(f'epio_ep_rank_us{{{E},kernel="dispatch"}}', "{{rank}}（{{window}}）")], 12, y,
                      unit="µs", points=True))
    y += 8
    P.append(row("KV / IO（注入的 KV，每次传输一个点）", y)); y += 1
    P.append(ts_panel("KV 每次传输：耗时和延迟的 p50 / p99（30 秒滑动窗口，ms）",
                      [tgt(kv_q(0.5, "transfer"), "传输耗时 p50", "A"), tgt(kv_q(0.99, "transfer"), "传输耗时 p99", "B"),
                       tgt(kv_q(0.5, "latency"), "到达到完成 p50", "C"), tgt(kv_q(0.99, "latency"), "到达到完成 p99", "D")],
                      0, y, unit="ms",
                      desc="传输耗时：开始传到传完；到达到完成：KV 到达（时间表上的时刻）到传完，含排队等待"))
    P.append(ts_panel("KV 吞吐（左，所有卡合计 GiB/s）/ 每秒传输次数（右，每张卡一次算一次）",
                      [tgt(f"sum(sum_over_time(epio_kv_mb{{{E}}}[10s])) / 10 / 1024", "KV GiB/s", "A"),
                       tgt(f'sum(count_over_time(epio_kv_ms{{{E},kind="transfer"}}[10s])) / 10', "传输次数/秒", "B")],
                      12, y, overrides=[right_axis("B")]))
    y += 8
    P.append(row("真实 PD（e2e/pd_proxy.py 的统计；decode-only 的实验这一行为空）", y)); y += 1

    def req_q(q, m):
        return f"histogram_quantile({q}, sum(histogram_over_time({m}{{{E}}}[30s])) by (vmrange))"

    P.append(ts_panel("每个请求：TTFT、prefill 耗时、decode 等 KV（TTFT 减 prefill）的 p50 / p99（30 秒滑动窗口，ms）",
                      [tgt(req_q(0.5, "epio_pd_ttft_ms"), "TTFT p50", "A"), tgt(req_q(0.99, "epio_pd_ttft_ms"), "TTFT p99", "B"),
                       tgt(req_q(0.5, "epio_pd_prefill_ms"), "prefill p50", "C"),
                       tgt(req_q(0.99, "epio_pd_prefill_ms"), "prefill p99", "D"),
                       tgt(req_q(0.5, "epio_pd_decode_wait_ms"), "decode 等 KV p50", "E"),
                       tgt(req_q(0.99, "epio_pd_decode_wait_ms"), "decode 等 KV p99", "F")], 0, y, unit="ms",
                      desc="READ 模式下 decode 等 KV = 读 KV 加第一步；WRITE 模式两条腿同时发，这一项只作参考"))
    P.append(ts_panel("每秒：prefill 开始 / 完成的请求，decode 开始的请求，decode 收到的 KV 块（右）",
                      [tgt(f"sum(epio_pd_prefill_starts_per_s{{{E}}})", "prefill 开始", "A"),
                       tgt(f"sum(epio_pd_prefill_done_per_s{{{E}}})", "prefill 完成", "B"),
                       tgt(f"sum by (server) (epio_pd_decode_starts_per_s{{{E}}})", "{{server}} decode 开始", "C"),
                       tgt(f"sum by (server) (epio_pd_kv_blocks_per_s{{{E}}})", "{{server}} KV 块", "D")],
                      12, y, overrides=[right_axis("D")]))
    y += 8
    P.append(row("网卡（ainic-netmon，每 2 ms 一个点；缩放到几秒以内能看到单个读数）", y)); y += 1
    P.append(ts_panel("decode 每张卡接收（每个时间点内的平均速率）",
                      [tgt(nic_avg("rx_rdma_ucast_bytes", "$decode") + " * 8", "{{host}} {{netdev}}")], 0, y, h=9,
                      unit="bps", interval="2ms"))
    P.append(ts_panel("decode 每张卡发送（EP）",
                      [tgt(nic_avg("tx_rdma_ucast_bytes", "$decode") + " * 8", "{{host}} {{netdev}}")], 12, y, h=9,
                      unit="bps", interval="2ms"))
    y += 9
    P.append(ts_panel("decode 每张卡接收：每个时间点内最高的 2 ms 速率（看 KV 尖峰）",
                      [tgt(nic_peak("rx_rdma_ucast_bytes", "$decode") + " * 8", "{{host}} {{netdev}}")], 0, y, h=9,
                      unit="bps", interval="2ms"))
    P.append(ts_panel("KV 来源每张卡发送",
                      [tgt(nic_avg("tx_rdma_ucast_bytes", "$source") + " * 8", "{{host}} {{netdev}}")], 12, y, h=9,
                      unit="bps", interval="2ms"))
    y += 9
    P.append(ts_panel("decode 收到的 CNP 和 ECN 标记（每秒，按节点合计）",
                      [tgt(f'sum by (host) ({nic_avg("rx_rdma_cnp_pkts", "$decode")})', "{{host}} CNP", "A"),
                       tgt(f'sum by (host) ({nic_avg("rx_rdma_ecn_pkts", "$decode")})', "{{host}} ECN", "B")],
                      0, y, interval="2ms",
                      desc="EP 的包在交换机里排在 KV 后面时被打 ECN 标记，decode 回 CNP"))
    P.append(ts_panel("重传包和 ACK 超时（每秒，按节点合计；TC 0 丢包时看这里）",
                      [tgt(f'sum by (host) ({nic_avg("tx_rdma_retx_pkts", ".*")})', "{{host}} 重传包", "A"),
                       tgt(f'sum by (host) ({nic_avg("tx_rdma_ack_timeout", ".*")})', "{{host}} ACK 超时", "B")],
                      12, y, interval="2ms"))
    y += 8
    P.append(ts_panel("PFC pause 帧（ethtool，每秒一个点，按节点合计）",
                      [tgt(f'sum by (host) (max_over_time(netmon_rate{{{E},source="ethtool",counter=~"frames_(tx|rx)_(pri)?pause",resolution="normal"}}[$__interval]))',
                           "{{host}}")], 0, y))
    y += 8
    P.append(row("每个窗口的汇总（跑完后由 tools/epio_summary.py 计算）", y)); y += 1
    P.append({"type": "table", "title": "每个窗口相对 base 的变化、网卡和 KV", "datasource": DS,
              "gridPos": {"x": 0, "y": y, "w": 24, "h": 9},
              "targets": [tgt(f"last_over_time(epio_summary{{{E}}}[$__range])", "", "A", instant=True, fmt="table")],
              "transformations": [{"id": "groupingToMatrix",
                                   "options": {"columnField": "metric", "rowField": "window", "valueField": "Value"}}],
              "fieldConfig": {"defaults": {"decimals": 1}, "overrides": []}, "options": {"showHeader": True}})
    y += 9
    P.append({"type": "table", "title": "跨实验对比：选几个实验（上面的“对比”），每行一个实验的一个窗口", "datasource": DS,
              "gridPos": {"x": 0, "y": y, "w": 24, "h": 10},
              "targets": [tgt('label_join(last_over_time(epio_summary{experiment=~"$compare"}[90d]), "row", " / ", '
                              '"experiment", "window")', "", "A", instant=True, fmt="table")],
              "transformations": [{"id": "groupingToMatrix",
                                   "options": {"columnField": "metric", "rowField": "row", "valueField": "Value"}}],
              "fieldConfig": {"defaults": {"decimals": 1}, "overrides": []}, "options": {"showHeader": True}})
    templ = [
        {"name": "experiment", "label": "实验", "type": "query", "datasource": DS,
         "query": {"query": "label_values(epio_node, experiment)", "refId": "V"}, "refresh": 2, "sort": 2},
        {"name": "decode", "label": "decode 节点", "type": "query", "datasource": DS, "multi": True, "includeAll": True,
         "allValue": ".*", "current": {"text": "All", "value": "$__all"},
         "query": {"query": f'label_values(epio_node{{{E},role="decode"}}, host)', "refId": "V"}, "refresh": 2},
        {"name": "source", "label": "KV 来源", "type": "query", "datasource": DS, "multi": True, "includeAll": True,
         "allValue": ".*", "current": {"text": "All", "value": "$__all"},
         "query": {"query": f'label_values(epio_node{{{E},role="kv_source"}}, host)', "refId": "V"}, "refresh": 2},
        {"name": "compare", "label": "对比", "type": "query", "datasource": DS, "multi": True, "includeAll": True,
         "allValue": ".*", "current": {"text": "All", "value": "$__all"},
         "query": {"query": "label_values(epio_summary, experiment)", "refId": "V"}, "refresh": 1, "sort": 2},
    ]
    for i, p in enumerate(P, 1):
        p["id"] = i
    return {"uid": "epio", "title": "EP × KV 实验（smci355）", "tags": ["epio", "netmon"], "schemaVersion": 39,
            "timezone": "browser", "refresh": "10s", "time": {"from": "now-1h", "to": "now"},
            "templating": {"list": templ}, "panels": P, "editable": True,
            "links": [{"title": "netmon 网卡详情", "type": "link", "url": "/d/netmon-nic/netmon-nic-detail?var-experiment=${experiment}&${__url_time_range}"}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  "monitor", "grafana-conf", "dashboards", "epio.json"))
    a = ap.parse_args()
    json.dump(build(), open(a.out, "w"), ensure_ascii=False, indent=1)
    print(a.out)


if __name__ == "__main__":
    main()
