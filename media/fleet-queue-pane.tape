# The demo README.md embeds. Regenerate from the checkout root with:
#
#     vhs media/fleet-queue-pane.tape
#
# then shrink it (the raw capture is roughly twice the size):
#
#     ffmpeg -i media/fleet-queue-pane.gif \
#       -vf "fps=10,scale=1000:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=none" \
#       -loop 0 -y media/fleet-queue-pane.gif
#
# IT RECORDS THE REAL QUEUE AND THE REAL SESSION LIST, and that is the point.
# An earlier take drove the pane from a fixture through FLEET_QUEUE_DIR and left
# the Sessions column to whatever the machine had; the column and the queue then
# disagreed, and a redaction pass over the column left it blank beside a status
# bar reading "3 session(s)" — which reads as a rendering bug rather than a
# demo. The queue pane sits BESIDE the session list and only makes sense with
# both filled by the same run of work: three workers in the list, the same three
# topics dispatched in the pane.
#
# So record this while something is actually in flight. `./scripts/queue.sh
# list` says whether anything is; a queue with nothing running makes a dull and
# slightly dishonest picture. Whoever regenerates it should read their own
# session titles first — they are real, and this file is published.
#
# ~107 COLUMNS ON PURPOSE. layout.lua drops the pipelines column below
# PIPELINES_MIN_COLS (120) and keeps side columns above two_panel_min_cols (80),
# so the queue pane is the only side column here without disabling a plugin or
# writing to anyone's config.
#
# THE BOOT AND THE CENTRE SWITCH HAPPEN BEFORE `Show`. Ten seconds of empty
# terminal was most of the file size, and the centre pane is otherwise a live
# agent transcript — the session list is the operator's own work titles, which
# is what makes the demo coherent, but a transcript is a different thing.
#
# DRIVEN THROUGH Ctrl+P because vhs has no F-key command and its Type sends
# escape sequences literally. The pane declares a `commands` entry, so the
# palette is a real user path; F3 is the chord a person presses.
Output media/fleet-queue-pane.gif
Set Shell bash
Set FontSize 16
Set Width 1120
Set Height 760
Set Padding 10
Set Framerate 10
Hide
Type "clear"
Enter
Sleep 500ms
Type "thurbox"
Enter
Sleep 10s
Ctrl+P
Sleep 800ms
Type "shell tab"
Sleep 800ms
Enter
Sleep 1500ms
Type "clear"
Enter
Sleep 1200ms
Show
Sleep 1500ms
Ctrl+P
Sleep 800ms
Type "fleet queue"
Sleep 800ms
Enter
Sleep 8s
Ctrl+P
Sleep 700ms
Type "fleet queue"
Sleep 700ms
Enter
Sleep 2500ms
