#!/usr/bin/env bash
# DCQCN profile binding of this host's ionic ports for one run. Runs on the host (hx), not in the container; needs sudo -n.
#   dcqcn_bind.sh save <dir>       keep every port's match_default / match_rules in <dir>/<host> (a state already saved there
#                                  is kept: it is the one from before the run)
#   dcqcn_bind.sh set "<rules>"    write <rules> to every port's match_rules ("prio 0x8 1": QPs at priority 3, i.e. TC 104,
#                                  get profile 1, the one `nicctl show dcqcn` reports Enabled; by default they get profile 0)
#   dcqcn_bind.sh restore <dir>    write the saved values back, check them, drop the saved state (nothing saved: no-op)
#   dcqcn_bind.sh show [<json>]    the ports' selection, the profile every QP got (debugfs) and, from nicctl, how many RC QPs
#                                  have a nonzero token_bucket_size / rate_enforced / target_rate / alpha; <json> keeps nicctl's output
# Only QPs created after a change get the new profile.
set -u
SYS=${DCQCN_SYSFS:-/sys/class/infiniband}
SUDO=${DCQCN_SUDO-sudo -n}
cmd=${1:?usage: dcqcn_bind.sh save|set|restore|show ...}
shift
ports() { ls -d "$SYS"/ionic_* 2> /dev/null; }
put() { echo "$2" | $SUDO tee "$1" > /dev/null; }
# a file's words on one line: an emptied rule list reads back as a blank line
norm() { tr -s ' \n' '  ' < "$1" | sed 's/^ *//; s/ *$//'; }

case $cmd in
save)
    d=${1:?dir}/$(hostname -s)
    if [ -e "$d/saved" ]; then echo "keeping the state saved $(cat "$d/saved")"; exit 0; fi
    mkdir -p "$d"
    for p in $(ports); do
        cat "$p/dcqcn/match_default" > "$d/${p##*/}.default"
        cat "$p/dcqcn/match_rules" > "$d/${p##*/}.rules"
    done
    date -u +%FT%TZ > "$d/saved"
    echo "saved $(ports | wc -l) ports" ;;
set)
    # [<default>]: match_default too, the profile of a QP no rule selects. MoRI's QPs carry SL 0 (only the traffic
    # class is set), so a "prio 0x8" rule misses them and only match_default 1 puts them on profile 1
    rules=${1:?rules} def=${2:-}
    for p in $(ports); do
        put "$p/dcqcn/match_rules" "$rules" || { echo "FAILED: $p"; exit 1; }
        if [ -n "$def" ]; then put "$p/dcqcn/match_default" "$def" || { echo "FAILED: $p"; exit 1; }; fi
    done
    echo "match_rules '$rules'${def:+ match_default $def} on $(ports | wc -l) ports" ;;
restore)
    d=${1:?dir}/$(hostname -s)
    [ -e "$d/saved" ] || { echo "nothing saved"; exit 0; }
    bad=0
    for p in $(ports); do
        n=${p##*/}
        put "$p/dcqcn/match_default" "$(cat "$d/$n.default")"
        # one rule per line; an empty write clears them
        put "$p/dcqcn/match_rules" ""
        while IFS= read -r r; do [ -n "$r" ] && put "$p/dcqcn/match_rules" "$r"; done < "$d/$n.rules"
        if [ "$(norm "$p/dcqcn/match_default")" != "$(norm "$d/$n.default")" ] ||
            [ "$(norm "$p/dcqcn/match_rules")" != "$(norm "$d/$n.rules")" ]; then
            echo "DIFFERS after restoring: $n"
            bad=1
        fi
    done
    if (( bad )); then exit 1; fi
    rm -rf "$d"
    echo "restored $(ports | wc -l) ports" ;;
show)
    for p in $(ports); do
        echo "${p##*/} match_default=$(cat "$p/dcqcn/match_default") match_rules=[$(tr '\n' ';' < "$p/dcqcn/match_rules")]"
    done
    echo "QP profiles (debugfs):"
    $SUDO bash -c 'grep -h dcqcn_profile /sys/kernel/debug/ionic/*/lif0/rdma/qp/*/info' 2> /dev/null | sort | uniq -c
    j=${1:-/tmp/dcqcn-qp-$$.json}
    $SUDO nicctl show rdma queue-pair --state active --detail -j > "$j" 2> /dev/null
    echo "firmware DCQCN state of the RC QPs (nicctl):"
    python3 - "$j" << 'EOF'
import re, sys
t = open(sys.argv[1]).read()
for k in ("token_bucket_size", "rate_enforced", "target_rate", "rate_reduction_factor_alpha_value"):
    v = [int(x) for x in re.findall(r'"%s"\s*:\s*"?(\d+)' % k, t)]
    print(f"  {k}: {len(v)} QPs, {sum(1 for x in v if x)} nonzero, max {max(v) if v else '-'}")
EOF
    [ $# -ge 1 ] || rm -f "$j" ;;
*)
    echo "unknown: $cmd"; exit 2 ;;
esac
