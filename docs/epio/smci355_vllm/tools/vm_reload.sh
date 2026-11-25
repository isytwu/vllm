#!/usr/bin/env bash
# Runs on the LOGIN node: nohup bash tools/vm_reload.sh <run> [<run> ...] > logs/vm_reload.log 2>&1 &
# For each run in turn, waits until its phase is over (results/e2e/<run>/summary.md written by the launcher, nicmon
# traces unchanged for a minute), then replaces the run's series in VictoriaMetrics with a full load of its traces
# (tools/nicmon_to_vm.py), so Grafana shows the whole run, including what the nodes' file caches held back.
set -uo pipefail
W=/apps/yutongwu/store-vllm/k31-vllm-ep-io-smci355-20261119
VM=${VM:-http://127.0.0.1:28528}
for run in "$@"; do
    rd=$W/results/e2e/$run
    echo "$(date -u +%FT%TZ) waiting for $run"
    while :; do
        if [ -s "$rd/summary.md" ]; then
            m1=$(stat -c %Y "$rd"/nicmon_*.csv.gz 2> /dev/null | sort -n | tail -1)
            sleep 60
            m2=$(stat -c %Y "$rd"/nicmon_*.csv.gz 2> /dev/null | sort -n | tail -1)
            [ "$m1" = "$m2" ] && break
        else
            sleep 60
        fi
    done
    curl -s -m 60 "$VM/api/v1/admin/tsdb/delete_series" --data-urlencode "match[]={run=\"$run\"}"
    python3 "$W/tools/nicmon_to_vm.py" "$rd" --vm "$VM"
    echo "$(date -u +%FT%TZ) reloaded $run"
done
