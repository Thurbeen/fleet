#!/usr/bin/env sh
# The demo's stand-in agent: prints the transcript named after this session's
# directory and waits. record.sh says why, and reports each session's status.
#
# A worker keeps a spinner turning, because thurbox reads a `working` session
# whose pane has gone quiet as idle; the lead sits at its prompt.
name=$(basename "$PWD")
printf '\033[2J\033[H'
cat "$FLEET_DEMO_TRANSCRIPTS/$name.txt"
if [ "$name" = fleet ]; then
    exec tail -f /dev/null
fi
started=$(date +%s)
while :; do
    for frame in ⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏; do
        elapsed=$(($(date +%s) - started + 2412))
        printf '\r  \033[33m%s working · %dm %02ds\033[0m ' "$frame" $((elapsed / 60)) $((elapsed % 60))
        sleep 0.2
    done
done
