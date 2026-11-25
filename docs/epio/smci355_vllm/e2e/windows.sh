# Sourced by e2e/e1.sh and e2e/e2.sh (login node, after mb/lib.sh): the measurement windows against a running decode.
# Needs DECODE (the decode nodes), C (the IO peer), OUT, IOTC, WIN, GAP, CYCLES and PROF_URLS ("host:port ..." of the
# decode API servers that take /start_profile, /stop_profile; empty = no captures). TARGETS (default: every decode
# node) are the decode nodes the IO goes to. Windows of WIN s alternate with GAP s of no IO; WINDOWS (default
# "base IWk IRk IWk10") picks them:
#   base    no IO
#   IWk     KV-shaped RDMA writes from C's GPUs into the targets' GPUs (io_inject, back to back; the PD WRITE mode)
#   IRk     the targets pull KV-shaped RDMA reads from C's GPUs (the PD READ mode)
#   IWk10   the writes at 10 GB/s per NIC
#   IWhR    the writes into the targets' pinned host memory on the far socket (KV landing in DRAM across NUMA)
#   IWkT0   IWk with the IO in TC 0 (the lossy class)
#   IWk1 / IRk1  IWk / IRk into the first target only: all of C's NICs on one decode node
#   IRk5 / IRk10 the reads at 5 / 10 GB/s per NIC; IRkT0 the reads in TC 0
#   IWk5 / IWk2 / IWk1g  the writes at 5 / 2 / 1 GB/s per NIC (in e1ra_6857 10 GB/s already cost +72 %)
#   IWk1gS / IWk1gB      1 GB/s per NIC in 1 MB bursts every ms / 64 MB bursts every 64 ms (IWk1g: 16 MB every 16 ms)
#   IWk10S / IWk5S / IWkS  10 / 5 GB/s per NIC in 1 MB bursts (every 0.1 / 0.2 ms), and 1 MB bursts back to back
#                          (io_inject keeps one burst per QP in flight: the burst is what each NIC has in flight)
#   IWk10c2 / c4 / c8      10 GB/s per NIC in 2 / 4 / 8 MB bursts (in e1_6866 1 MB cost nothing, 16 MB +72 %)
#   IWkSi4 / IWkSi16       1 MB bursts back to back with 4 / 16 of them in flight (4 / 16 MB in flight per NIC)
#   IRkS / IRk10S          the reads in 1 MB bursts back to back / at 10 GB/s per NIC
#   IWkR16 / IWkR64        one 16 / 64 MB burst per NIC per second: roughly a 4k / 16k-token prompt's KV (576 B per
#                          token and layer, DCP 8 spreads it over the DP rank's 8 GPUs) landing once a second
#   IWkP<ms>               one 16 MB burst per NIC every <ms> ms into every target (16 / <ms> GB/s per NIC; IWk1g is
#                          IWkP16, IWkR16 IWkP1000): where between those two 16 MB bursts start to cost
#   SPIN    no IO: 8 busy loops on each target in a job step, i.e. on the CPUs io_inject's processes get (each of
#           them spins a core polling its CQs; the vLLM workers are pinned to the same socket)
#   IWk0    io_inject's receivers set up on the targets (GPU buffers registered, CQs polled), one 16 MB burst per
#           rail, then nothing: the receivers without the traffic
#   IWkN1   IWk with the receivers moved to socket 1 (taskset -c 64-127), away from the vLLM workers
#   IWk1x2 / IWk1x3        incast: the first 2 / 3 IO sources (C, then SOURCES) write the first target at once, back to
#                          back; suffix T0 = in TC 0, S = in 1 MB batches, p<n> = each flow at <n> GB/s per NIC
#                          (IWk1x2p10: 20 GB/s into each of the target's NICs, 40 % of line rate)
#   IRk1x2 / IRk1x3        the same with the first target reading the 2 / 3 sources at once
#   MK[R][Hn|Hf][x<k>[i]|B][T0][l|h][p<n>|s|c<n>|g]  KV transfers shaped like Moonshot's decode-side 10 ms traces (io_inject's KV mode): into each
#                          target, MK_RATE (6.7) transfers per second, each MK_MB (1024) MB on each of MK_NICS (3) of
#                          its 8 NICs, as MK_CHUNK_MB (16) MB batches, MK_INFLIGHT (2) in flight, at line rate (about
#                          2.5 transfers / s / NIC: 300-400 Gb/s spikes of 20-30 ms). R = the target reads them from C
#                          (MoRIIO's direction), T0 = in TC 0, l / h = a quarter / twice the rate, p<n> = each NIC
#                          capped at <n> GB/s within a transfer, s = 1 MB batches one at a time, c<n> = <n> MB batches
#                          one at a time, g = only as the decode starts a step, at most MK_GATE_MB (256) MB per NIC
#                          per step (the serve's YT_STEP_TICK_PORT = MK_GATE_PORT, 47000; the ticks of DP rank 0, the
#                          first decode node: target it alone, TARGETS), Hn / Hf = into the
#                          target's pinned host memory on its NIC's NUMA node / the other one (a KV store's DRAM pool,
#                          like Mooncake Store's) instead of its GPUs, x<k> = the first k IO sources (C, then SOURCES)
#                          into the first target, each at 1/k of the rate (the same bytes as one source), i = all of
#                          them on one schedule (every transfer lands on the same NICs from every source at once: k:1
#                          incast), B = the second IO source alone. A source that is a decode node sends from host memory
#                          (its GPUs are the decode's)
# logs/windows_<run>.txt (e.g. windows_e1m_6855.txt), when it exists, replaces WINDOWS (and WINDOWS_ADD) for that run.
#   IRb / IWb    mori's IO benchmark (8 GPU pairs, 32 KB transfers, batches of 8192) between C and every target: the
#                targets read C (IRb) or C writes them (IWb). In the microbenchmarks IRb's pattern (thousands of small
#                READs in flight) slowed EP 10-40x where KV-shaped reads at a higher rate cost 10-20 %
#   IRb1 / IWb1  the same on the first target only (all of C's NICs on one decode node)
# Every window gets a torch-profiler capture on the decode servers in its middle; events.tsv has the window and
# capture times. snaps.jsonl has every node's RDMA counters (mb/snap.sh: CNPs and ECN-marked packets received,
# retransmissions, PFC) once the IO runs (<window>_<cycle>_a) and before it stops (_b).
EV=$OUT/events.tsv
# EPIO_PUSH=<VictoriaMetrics URL>: every event also goes there (epio_event), and the windows as states (epio_window 1 at
# a window's start, 0 at its end; epio_io 1 / 0 while its IO runs), under experiment $EPIO_EXP, for the experiment's
# Grafana dashboard (tools/epio_dash.py). Posted in the background: a slow VictoriaMetrics never holds a run up.
epio_lines() {
    [ -n "${EPIO_PUSH:-}" ] || return 0
    printf '%s\n' "$@" | curl -s -m 5 --data-binary @- "$EPIO_PUSH/api/v1/import/prometheus" > /dev/null 2>&1 &
}
ev() {
    local ts; ts=$(date +%s.%N)
    printf '%s\t%s\t%s\n' "$ts" "$1" "$2" | tee -a "$EV"
    [ -n "${EPIO_PUSH:-}" ] || return 0
    local ms=${ts%.*}${ts#*.} l="experiment=\"${EPIO_EXP:-$RUN}\",run=\"$RUN\"" out=()
    ms=${ms:0:13}
    out+=("epio_event{$l,name=\"$1\"} 1 $ms")
    case $2 in start) out+=("epio_window{$l,name=\"$1\"} 1 $ms") ;; end) out+=("epio_window{$l,name=\"$1\"} 0 $ms") ;; esac
    case $1 in
        *_io_on) out+=("epio_io{$l,name=\"${1%_io_on}\"} 1 $ms") ;;
        *_io_end) out+=("epio_io{$l,name=\"${1%_io_end}\"} 0 $ms") ;;
    esac
    epio_lines "${out[@]}"
}
IOB="--enable-batch-transfer --enable-sess --buffer-size 32768 --transfer-batch-size 8192 --iters 1000000000 --num-initiator-dev 8 --num-target-dev 8"

profile() {
    [ -n "${PROF_URLS:-}" ] || return 0
    local cp=() u
    ev "prof_$1" start
    for u in $PROF_URLS; do hx "${u%%:*}" "curl -s -m 30 -X POST http://127.0.0.1:${u##*:}/start_profile" > /dev/null & cp+=($!); done
    wait "${cp[@]}"
    sleep "${PROFILE_S:-8}"
    cp=()
    for u in $PROF_URLS; do hx "${u%%:*}" "curl -s -m 600 -X POST http://127.0.0.1:${u##*:}/stop_profile" > /dev/null & cp+=($!); done
    wait "${cp[@]}"
    ev "prof_$1" end
    # the capture's MoRI dispatch / combine kernel times to the dashboard (tools/ep_push.py parses its traces)
    [ -n "${EPIO_PUSH:-}" ] && nohup python3 "$W/tools/ep_push.py" "$OUT" "prof_$1" --push "$EPIO_PUSH" \
        --experiment "${EPIO_EXP:-$RUN}" >> "$OUT/ep_push.log" 2>&1 < /dev/null &
    return 0
}

# io_nodes: C and the other IO sources (SOURCES, e2e/e1.sh), each once, C first
io_nodes() { printf '%s\n' "$C" ${SOURCES:-} | awk 'NF && !s[$0]++'; }
win_rates() { local n s=""; for n in $DECODE $(io_nodes); do s="$s$n $(rate "$n" 2) | "; done; echo "$s"; }
snaps() { local n; for n in $DECODE $(io_nodes); do hx "$n" "bash $W/mb/snap.sh $1" >> "$OUT/snaps.jsonl" 2> /dev/null; done; }

# alive: the decode streamed something in the last minute (the loadgen's 10 s records); a hung decode (e1_6857 IWb,
# e1x_6857 ramp-up) ends the run instead of idling through the remaining windows
alive() {
    local recent
    recent=$(tail -6 "$OUT/load/live.jsonl" 2> /dev/null | python3 -c "import sys, json; print(sum(json.loads(l)['gaps'] for l in sys.stdin))" 2> /dev/null)
    [ "${recent:-1}" != 0 ]
}

window() {
    local name=$1 cyc=$2 per=0 mb=16 inf=1 tc=$IOTC t i tg=(${TARGETS:-$DECODE}) srcs=($(io_nodes)) k=0 mod=""
    alive || { ev stalled "no streamed chunks in the last minute before $name"; exit 3; }
    # IWk1x<k>[T0|S|p<GB/s>]: k IO sources write the first target at once (k:1 incast), back to back / in TC 0 / in 1 MB
    # batches / each flow at <GB/s>; IRk1x<k>[...]: the first target reads the k sources at once instead
    if [[ $name =~ ^I[WR]k1x([2-9])(T0|S|p[0-9]+)?$ ]]; then
        k=${BASH_REMATCH[1]} mod=${BASH_REMATCH[2]}
        (( k <= ${#srcs[@]} )) || { ev "$name" "skipped: $k IO sources wanted, ${#srcs[@]} given"; return 0; }
    fi
    local kvargs="" kvmem="" kvsrc=0 s pm im
    if [[ $name =~ ^MK(R?)(H[nf])?(x[2-9]i?|B)?(T0)?([lh]?)(p[0-9]+|s|c[0-9]+|g)?$ ]]; then
        local xs=${BASH_REMATCH[3]} lv=${BASH_REMATCH[5]} md=${BASH_REMATCH[6]} rate=${MK_RATE:-6.7} rd=${BASH_REMATCH[1]}
        [ -n "${BASH_REMATCH[4]}" ] && tc=0
        case ${BASH_REMATCH[2]} in Hn) kvmem="--mem host --numa local" ;; Hf) kvmem="--mem host --numa remote" ;; esac
        case $lv in l) rate=$(awk -v r="$rate" 'BEGIN { print r / 4 }') ;; h) rate=$(awk -v r="$rate" 'BEGIN { print r * 2 }') ;; esac
        mb=${MK_CHUNK_MB:-16} inf=${MK_INFLIGHT:-2}
        case $md in
            s) mb=1 inf=1 ;; c*) mb=${md#c} inf=1 ;; p*) kvargs="--pace-gbps ${md#p}" ;;
            # g: the decode's DP rank 0 sends its step ticks to itself and to C (e2e/e1.sh YT_STEP_TICK_HOSTS)
            g) kvargs="--gate-port ${MK_GATE_PORT:-47000} --gate-mb ${MK_GATE_MB:-256}"
               [ -z "$rd" ] && kvargs="$kvargs --gate-bind 0.0.0.0" ;;
        esac
        case $xs in
            x*) k=${xs:1:1}
                (( k <= ${#srcs[@]} )) || { ev "$name" "skipped: $k IO sources wanted, ${#srcs[@]} given"; return 0; }
                tg=("${tg[0]}") kvsrc=$(seq -s ' ' 0 $((k - 1)))
                rate=$(awk -v r="$rate" -v k="$k" 'BEGIN { print r / k }')
                [[ $xs == *i ]] && kvargs="--kv-shared $kvargs" ;;
            B)  (( ${#srcs[@]} >= 2 )) || { ev "$name" "skipped: a second IO source wanted"; return 0; }
                tg=("${tg[0]}") kvsrc=1 ;;
        esac
        kvargs="--kv-mb ${MK_MB:-1024} --kv-rate $rate --kv-nics ${MK_NICS:-3} $kvargs"
    fi
    ev "$name" start
    EPIO_WIN=$name
    case "$name" in
        I[WR]k1x[2-9]*)
            tg=("${tg[0]}")
            case $mod in T0) tc=0 ;; S) mb=1 ;; p*) per=$(awk -v r="${mod#p}" 'BEGIN { printf "%g", 16 / r }') ;; esac ;;
        IWk1T0) tc=0 tg=("${tg[0]}") ;;
        IWk1S) mb=1 tg=("${tg[0]}") ;;
        IWk1p20) per=0.8 tg=("${tg[0]}") ;;
        IWk1p10) per=1.6 tg=("${tg[0]}") ;;
        IWk1p5) per=3.2 tg=("${tg[0]}") ;;
        IWk1p2) per=8 tg=("${tg[0]}") ;;
        IWk10|IRk10) per=1.6 ;;
        IRk5|IWk5) per=3.2 ;;
        IWk2) per=8 ;;
        IWk1g) per=16 ;;
        IWk1gS) per=1 mb=1 ;;
        IWk1gB) per=64 mb=64 ;;
        IWk10S|IRk10S) per=0.1 mb=1 ;;
        IWk5S) per=0.2 mb=1 ;;
        IWkS|IRkS) mb=1 ;;
        IWk10c2) per=0.2 mb=2 ;;
        IWk10c4) per=0.4 mb=4 ;;
        IWk10c8) per=0.8 mb=8 ;;
        IWkSi4) mb=1 inf=4 ;;
        IWkSi16) mb=1 inf=16 ;;
        IWkR16) per=1000 mb=16 ;;
        IWkR64) per=1000 mb=64 ;;
        IWkP[0-9]*) per=${name#IWkP} ;;
        IWkT0|IRkT0) tc=0 ;;
        IWk1|IRk1|IRb1|IWb1) tg=("${tg[0]}") ;;
    esac
    case "$name" in
        IWk1x[2-9]*)
            # source i into the target's receivers of port block i (PRE_RECV) or into fresh ones
            for i in $(seq 0 $((k - 1))); do
                INJ_SRC_IDX=$i inj_start "$OUT/inj_${name}_$cyc/${srcs[$i]}" "$tc" write "${srcs[$i]}" "${tg[0]}" --chunk-mb $mb --period-ms $per --inflight $inf --duration $((WIN + 120))
            done ;;
        IRk1x[2-9]*)
            # one reader on the target per source; the readers come up mid-run (fresh processes on the decode node)
            for i in $(seq 0 $((k - 1))); do
                inj_start "$OUT/inj_${name}_$cyc/${srcs[$i]}" "$tc" read "${tg[0]}" "${srcs[$i]}" --chunk-mb $mb --period-ms $per --inflight $inf --duration $((WIN + 120))
            done ;;
        IWk|IWk10|IWkT0|IWk1|IWk1T0|IWk1S|IWk1p20|IWk1p10|IWk1p5|IWk1p2|IWk5|IWk2|IWk1g|IWk1gS|IWk1gB|IWk10S|IWk5S|IWkS|IWk10c2|IWk10c4|IWk10c8|IWkSi4|IWkSi16|IWkR16|IWkR64|IWkP[0-9]*)
            for t in "${tg[@]}"; do
                inj_start "$OUT/inj_${name}_$cyc/$t" "$tc" write "$C" "$t" --chunk-mb $mb --period-ms $per --inflight $inf --duration $((WIN + 120))
            done ;;
        MKR*)
            # the target reads into its own buffer (host memory: INJ_IMEM) from each source; a decode node serving the
            # reads holds them in host memory (INJ_PMEM)
            for t in "${tg[@]}"; do
                for i in $kvsrc; do
                    s=${srcs[$i]} pm="--mem gpu"
                    [[ " $DECODE " == *" $s "* ]] && pm="--mem host --numa local"
                    INJ_IMEM="${kvmem:---mem gpu}" INJ_PMEM="$pm" inj_start "$OUT/inj_${name}_$cyc/${t}_$s" "$tc" read "$t" "$s" --chunk-mb $mb --inflight $inf $kvargs --duration $((WIN + 120))
                done
            done ;;
        MK*)
            # host memory needs fresh receivers (PRE_RECV's hold GPU buffers); a decode node as the source sends from
            # host memory
            for t in "${tg[@]}"; do
                for i in $kvsrc; do
                    s=${srcs[$i]} im="--mem gpu"
                    [[ " $DECODE " == *" $s "* ]] && im="--mem host --numa local"
                    if [ -n "$kvmem" ]; then
                        INJ_NOPRE=1 INJ_PMEM="$kvmem" INJ_IMEM="$im" inj_start "$OUT/inj_${name}_$cyc/${t}_$s" "$tc" write "$s" "$t" --chunk-mb $mb --inflight $inf $kvargs --duration $((WIN + 120))
                    else
                        INJ_SRC_IDX=$i INJ_IMEM="$im" inj_start "$OUT/inj_${name}_$cyc/${t}_$s" "$tc" write "$s" "$t" --chunk-mb $mb --inflight $inf $kvargs --duration $((WIN + 120))
                    fi
                done
            done ;;
        IWhR)
            for t in "${tg[@]}"; do
                INJ_PMEM="--mem host --numa remote" inj_start "$OUT/inj_${name}_$cyc/$t" "$tc" write "$C" "$t" --chunk-mb 16 --period-ms 0 --duration $((WIN + 120))
            done ;;
        IWk0)
            # fresh receivers even when PRE_RECV pre-started others: GPU memory allocated and registered mid-run
            for t in "${tg[@]}"; do
                INJ_NOPRE=1 inj_start "$OUT/inj_${name}_$cyc/$t" "$tc" write "$C" "$t" --chunk-mb 16 --period-ms 3600000 --duration $((WIN + 120))
            done ;;
        IWkN1)
            for t in "${tg[@]}"; do
                INJ_PTASK="taskset -c 64-127" inj_start "$OUT/inj_${name}_$cyc/$t" "$tc" write "$C" "$t" --chunk-mb 16 --period-ms 0 --duration $((WIN + 120))
            done ;;
        SPIN)
            # inj_stop kills the steps with the rest of INJ_PIDS
            for t in "${tg[@]}"; do
                INJ_PIDS+=("$(cxbg "$t" "$OUT/spin_${cyc}_$t.log" "for i in 1 2 3 4 5 6 7 8; do timeout $((WIN + 60)) bash -c 'while :; do :; done' & done; wait")")
            done ;;
        IRk|IRk1|IRk5|IRk10|IRkT0|IRkS|IRk10S)
            for t in "${tg[@]}"; do
                inj_start "$OUT/inj_${name}_$cyc/$t" "$tc" read "$t" "$C" --chunk-mb $mb --period-ms $per --inflight $inf --duration $((WIN + 120))
            done ;;
        IRb|IWb|IRb1|IWb1)
            local op=read; [[ $name == IWb* ]] && op=write
            for t in "${tg[@]}"; do
                if [ $op = read ]; then
                    iob_start "$OUT/iob_${name}_$cyc/$t" "$tc" "$t" "$C" $IOB --op-type read
                else
                    iob_start "$OUT/iob_${name}_$cyc/$t" "$tc" "$C" "$t" $IOB --op-type write
                fi
            done
            sleep 25 ;;
    esac
    [ "$name" = base ] || { sleep 20; ev "${name}_io_on" "$(win_rates)"; }
    # the IO's QPs exist now: their DCQCN profile and state, next to the EP's
    case "$name" in IWk | IWk1 | IWk1x3) [ -n "${DCQCN_RULES:-}${DCQCN_SHOW:-}" ] && dcqcn_show "$OUT/dcqcn" "${name}_$cyc" $DECODE $(io_nodes) ;; esac
    snaps "${name}_${cyc}_a"
    sleep $((WIN / 2 - 20))
    profile "${name}_$cyc"
    sleep $((WIN / 2 - 20))
    snaps "${name}_${cyc}_b"
    [ "$name" = base ] || { ev "${name}_io_end" "$(win_rates)"; inj_stop $DECODE $(io_nodes); iob_stop $DECODE "$C"; }
    ev "$name" end
    tail -1 "$OUT/load/live.jsonl" 2> /dev/null
}

run_windows() {
    local cyc name list
    if [ -s "$W/logs/windows_$RUN.txt" ]; then WINDOWS=$(cat "$W/logs/windows_$RUN.txt"); WINDOWS_ADD=""; fi
    # IWb last: in e1_6857 the decode hung in it (an RCCL all-gather never completed, 600 s watchdog), so whatever
    # follows it would be lost
    # WINDOWS_ADD (default IWk IWk10, the clean large effect of e1_6857 and its 10 GB/s version) joins every list that
    # lacks them, so every E1 run can be compared on them
    local all="${WINDOWS:-base IRb IWk IRk IWb IWk10}" w
    for w in ${WINDOWS_ADD-IWk IWk10}; do [[ " $all " == *" $w "* ]] || all="$all $w"; done
    list="$(for name in $all; do [[ $name == IWb* ]] || printf '%s ' "$name"; done)"
    list="$list$(for name in $all; do [[ $name == IWb* ]] && printf '%s ' "$name"; done)"
    for cyc in $(seq 1 "$CYCLES"); do
        for name in $list; do
            window "$name" "$cyc"
            sleep "$GAP"
        done
    done
    window base $((CYCLES + 1))
}
