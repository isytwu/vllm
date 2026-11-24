#!/usr/bin/env bash
# Runs on the LOGIN node:  nohup bash tools/cap_daytime.sh <deadline, UTC> <job ...> > logs/cap_daytime.log 2>&1 &
# Once each job starts, lowers its time limit so it ends by the deadline (e.g. 2026-11-25T00:00:00 = 08:00 Beijing):
# the nights are ours, the days are not. tools/launch.sh skips the phases that no longer fit and cancels the job.
set -uo pipefail
export PATH=/opt/slurm/bin:$PATH
DL=$(date -u -d "${1:?deadline (UTC, e.g. 2026-11-25T00:00:00)}Z" +%s); shift
mins() { awk -v t="$1" 'BEGIN { d = 0; if (index(t, "-")) { split(t, a, "-"); d = a[1]; t = a[2] }
    n = split(t, p, ":"); s = 0; for (i = 1; i <= n; i++) s = s * 60 + p[i]; print int((d * 86400 + s) / 60) }'; }
declare -A seen
while :; do
    open=0
    for j in "$@"; do
        [ -n "${seen[$j]:-}" ] && continue
        st=$(squeue -h -j "$j" -o %T 2> /dev/null)
        [ -n "$st" ] || { seen[$j]=gone; echo "$(date -u +%FT%TZ) job $j gone"; continue; }
        open=1
        [ "$st" = RUNNING ] || continue
        start=$(date -u -d "$(squeue -h -j "$j" -o %S)Z" +%s)
        want=$(( (DL - start) / 60 )) cur=$(mins "$(squeue -h -j "$j" -o %l)")
        if (( want < cur )); then
            scontrol update JobId="$j" TimeLimit="$(( want > 1 ? want : 1 ))" \
                && echo "$(date -u +%FT%TZ) job $j: time limit ${cur} -> ${want} min (ends by $(date -u -d @"$DL" +%FT%TZ))"
        else
            echo "$(date -u +%FT%TZ) job $j: its ${cur} min already end by the deadline"
        fi
        seen[$j]=capped
    done
    (( open )) || break
    sleep 60
done
