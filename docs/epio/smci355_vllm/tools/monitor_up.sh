#!/usr/bin/env bash
# Runs on the LOGIN node:  bash tools/monitor_up.sh [results/e2e/<run> ...]
# A run with ainic-netmon recordings (<run>/netmon/<host>, NETMON=1) goes in with the recorder's own importer
# (tools/netmon_report/ainic_netmon_import.py; the netmon overview / NIC detail dashboards); older runs with
# tools/nicmon.py traces with tools/nicmon_to_vm.py (the nicmon dashboard).
# VictoriaMetrics ($VM_PORT, 28528, on 127.0.0.1 and this node's management address so that the job's nodes can push
# to it live; its data in $VM_DATA, /var/tmp/yutongwu_epio/vmdata on the login node's local disk) and Grafana
# ($GRAFANA_ADDR:$GRAFANA_PORT, 0.0.0.0:23300; anonymous viewers), the programs under monitor/, downloaded on first use;
# then each run's nicmon traces go into VictoriaMetrics (tools/nicmon_to_vm.py) and the nicmon dashboard is provisioned
# (tools/grafana_dash.py, its default time range on the first run's first MK window). The corporate network reaches
# this login node on port 80 only (ditian12's netmon Grafana): forward the port (Cursor / VS Code: Ports) and open
# http://localhost:23300/. Other users run their own VictoriaMetrics on this node (8428, 18428, 38428): the script
# checks that the one on $VM_PORT stores into $VM_DATA and that the Grafana on $GRAFANA_PORT is its own, and stops
# otherwise. The admin password goes into monitor/grafana-conf/admin_password.
set -euo pipefail
W=$(cd "$(dirname "$0")/.." && pwd)
M=$W/monitor
VP=${VM_PORT:-28528} GP=${GRAFANA_PORT:-23300} GA=${GRAFANA_ADDR:-0.0.0.0} VD=${VM_DATA:-/var/tmp/yutongwu_epio/vmdata}
ip=$(ip -4 -o addr show 2>/dev/null | awk '$2 !~ /^(lo|docker|br-|tunl|benic)/ {sub("/.*", "", $4); print $4; exit}')
mkdir -p "$M/grafana-conf/provisioning/datasources" "$M/grafana-conf/provisioning/dashboards" \
    "$M/grafana-conf/dashboards" "$M/grafana-data"
cd "$M"
latest() { curl -s "https://api.github.com/repos/$1/releases/latest" | python3 -c "import sys, json; print(json.load(sys.stdin)['tag_name'])"; }
if [ ! -x victoria-metrics-prod ]; then
    v=$(latest VictoriaMetrics/VictoriaMetrics)
    curl -sSL "https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/$v/victoria-metrics-linux-amd64-$v.tar.gz" | tar xz
fi
if ! ls -d grafana-*/ > /dev/null 2>&1; then
    g=$(latest grafana/grafana)
    curl -sSL "https://dl.grafana.com/oss/release/grafana-${g#v}.linux-amd64.tar.gz" | tar xz
fi
G=$(ls -d "$M"/grafana-*/ | head -1)
[ -s grafana-conf/admin_password ] || { python3 -c "import secrets; print(secrets.token_urlsafe(12))" > grafana-conf/admin_password; chmod 600 grafana-conf/admin_password; }
cat > grafana-conf/custom.ini << EOF
[server]
http_addr = $GA
http_port = $GP
[paths]
data = $M/grafana-data
logs = $M/grafana-data/log
plugins = $M/grafana-data/plugins
provisioning = $M/grafana-conf/provisioning
[security]
admin_user = admin
admin_password = $(cat grafana-conf/admin_password)
[auth.anonymous]
enabled = true
org_role = Viewer
[users]
allow_sign_up = false
[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
EOF
cat > grafana-conf/provisioning/datasources/vm.yaml << EOF
apiVersion: 1
datasources:
  - name: VictoriaMetrics
    uid: vm
    type: prometheus
    access: proxy
    url: http://127.0.0.1:$VP
    isDefault: true
    jsonData:
      timeInterval: 1ms
  - name: netmon-vm
    uid: netmon-vm
    type: prometheus
    access: proxy
    url: http://127.0.0.1:$VP
    isDefault: false
    jsonData:
      timeInterval: 1ms
EOF
cp "$W"/tools/netmon_report/dashboards/netmon_*.json grafana-conf/dashboards/
cat > grafana-conf/provisioning/dashboards/files.yaml << EOF
apiVersion: 1
providers:
  - name: nicmon
    type: file
    options:
      path: $M/grafana-conf/dashboards
EOF
# ours: the VictoriaMetrics on $VP stores into $VD (its /flags), the Grafana on $GP is the pid in grafana.pid
vm_ours() { curl -sf -m 5 "http://127.0.0.1:$VP/flags" 2> /dev/null | grep -q -- "-storageDataPath=\"$VD\""; }
gf_ours() { [ -s "$M/grafana.pid" ] && [ -O "/proc/$(cat "$M/grafana.pid")" ] && curl -sf -m 5 "http://127.0.0.1:$GP/api/health" > /dev/null; }
if ! vm_ours; then
    curl -s -m 5 -o /dev/null "http://127.0.0.1:$VP/health" && { echo "port $VP answers but is not our VictoriaMetrics: VM_PORT=<another> bash $0 ..."; exit 1; }
    mkdir -p "$VD"
    nohup ./victoria-metrics-prod -storageDataPath="$VD" -httpListenAddr="127.0.0.1:$VP${ip:+,$ip:$VP}" -retentionPeriod=1y \
        -search.latencyOffset=0s -search.maxPointsPerTimeseries=1000000 > "$M/vm.log" 2>&1 < /dev/null &
    echo $! > "$M/vm.pid"
    sleep 5
    vm_ours || { echo "VictoriaMetrics did not come up on $VP:"; tail -3 "$M/vm.log"; exit 1; }
fi
if ! gf_ours; then
    curl -s -m 5 -o /dev/null "http://127.0.0.1:$GP/api/health" && { echo "port $GP answers but is not our Grafana: GRAFANA_PORT=<another> bash $0 ..."; exit 1; }
    nohup "$G/bin/grafana" server --homepath "$G" --config "$M/grafana-conf/custom.ini" --pidfile "$M/grafana.pid" \
        > "$M/grafana.log" 2>&1 < /dev/null &
    sleep 30
    gf_ours || { echo "Grafana did not come up on $GP:"; grep -m3 -i -E 'error|address already in use' "$M/grafana.log"; exit 1; }
fi
for r in "$@"; do
    if [ -d "$r/netmon" ]; then
        python3 "$W/tools/netmon_report/ainic_netmon_import.py" import "$r"/netmon/* --url "http://127.0.0.1:$VP" \
            --grafana "http://$ip:$GP" --experiment "$(basename "$r")" --replace
    else
        python3 "$W/tools/nicmon_to_vm.py" "$r" --vm "http://127.0.0.1:$VP"
    fi
done
python3 "$W/tools/grafana_dash.py" ${1:+"$1"}
curl -s -X POST -u "admin:$(cat grafana-conf/admin_password)" "http://127.0.0.1:$GP/api/admin/provisioning/datasources/reload" > /dev/null
curl -s -X POST -u "admin:$(cat grafana-conf/admin_password)" "http://127.0.0.1:$GP/api/admin/provisioning/dashboards/reload" > /dev/null
echo "VictoriaMetrics: http://$ip:$VP (pid $(cat "$M/vm.pid" 2> /dev/null), data $VD)"
echo "Grafana: forward port $GP (Cursor / VS Code: Ports) and open http://localhost:$GP/ (pid $(cat "$M/grafana.pid"))"
