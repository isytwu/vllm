#!/usr/bin/env bash
# Runs on the LOGIN node:  bash tools/monitor_up.sh [results/e2e/<run> ...]
# VictoriaMetrics (127.0.0.1:$VM_PORT, 18428) and Grafana (127.0.0.1:$GRAFANA_PORT, 23300; anonymous viewers) under
# monitor/, downloaded on first use; then each run's nicmon traces go into VictoriaMetrics (tools/nicmon_to_vm.py)
# and the nicmon dashboard is provisioned (tools/grafana_dash.py, its default time range on the first run's first MK
# window). Both listen on localhost only: forward the Grafana port (Cursor / VS Code: Ports) and open
# http://localhost:23300/d/nicmon. The admin password is generated into monitor/grafana-conf/admin_password.
set -euo pipefail
W=$(cd "$(dirname "$0")/.." && pwd)
M=$W/monitor
VP=${VM_PORT:-18428} GP=${GRAFANA_PORT:-23300}
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
http_addr = 127.0.0.1
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
      timeInterval: 10ms
EOF
cat > grafana-conf/provisioning/dashboards/files.yaml << EOF
apiVersion: 1
providers:
  - name: nicmon
    type: file
    options:
      path: $M/grafana-conf/dashboards
EOF
if ! curl -sf "http://127.0.0.1:$VP/health" > /dev/null; then
    nohup ./victoria-metrics-prod -storageDataPath="$M/vmdata" -httpListenAddr="127.0.0.1:$VP" -retentionPeriod=1y \
        -search.latencyOffset=0s -search.maxPointsPerTimeseries=200000 > "$M/vm.log" 2>&1 < /dev/null &
    sleep 3
fi
# a port someone else on this login node already holds would make Grafana exit: check its health answers our version
if ! curl -sf "http://127.0.0.1:$GP/api/health" > /dev/null; then
    (cd "$M" && nohup "$G/bin/grafana" server --homepath "$G" --config "$M/grafana-conf/custom.ini" > "$M/grafana.log" 2>&1 < /dev/null &)
    sleep 30
    grep -q 'address already in use' "$M/grafana.log" && { echo "port $GP is taken: GRAFANA_PORT=<another> bash $0 ..."; exit 1; }
fi
for r in "$@"; do python3 "$W/tools/nicmon_to_vm.py" "$r" --vm "http://127.0.0.1:$VP"; done
python3 "$W/tools/grafana_dash.py" ${1:+"$1"}
curl -s -X POST -u "admin:$(cat grafana-conf/admin_password)" "http://127.0.0.1:$GP/api/admin/provisioning/dashboards/reload" > /dev/null
echo "Grafana: http://localhost:$GP/d/nicmon (forward port $GP)"
