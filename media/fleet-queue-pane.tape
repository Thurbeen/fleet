# The demo README.md embeds. Regenerate from the checkout root with:
#
#     vhs media/fleet-queue-pane.tape
#
# then shrink it (the raw capture is roughly twice the size). Write to a TEMP
# file and move it into place — ffmpeg reading and writing one path truncates
# its own input:
#
#     ffmpeg -i media/fleet-queue-pane.gif \
#       -vf "fps=10,scale=1000:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=64[p];[s1][p]paletteuse=dither=none" \
#       -loop 0 -y /tmp/pane.gif && mv /tmp/pane.gif media/fleet-queue-pane.gif
#
# 64 COLOURS AND SHORT DWELLS, because the agent tab costs what the empty shell
# did not. A transcript redraws more of the frame than a bare prompt, and a live
# agent's elapsed-time and token counters tick every single frame, so almost
# nothing compresses between frames. The shell take fit ~16 visible seconds at
# 128 colours in ~600K; this one runs ~11s at 64 and still weighs ~800K, and
# neither a lower framerate nor a shorter dwell buys much more — measured, 8fps
# saved 13K. Do not chase the old number by gutting the demo: TRIM DWELLS
# BEFORE YOU DROP THE F3 TOGGLE, and the toggle is the demo.
#
# IT RECORDS THE REAL QUEUE AND THE REAL SESSION LIST, and that is the point.
# An earlier take drove the pane from a fixture through FLEET_QUEUE_DIR and left
# the Sessions column to whatever the machine had; the column and the queue then
# disagreed, and a redaction pass over the column left it blank beside a status
# bar reading "3 session(s)" — which reads as a rendering bug rather than a
# demo. The queue pane sits BESIDE the session list and only makes sense with
# both filled by the same run of work: the workers in the list are the same ones
# whose topics the pane shows running.
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
# terminal was most of the file size, and neither the boot nor the tab switch is
# worth a frame.
#
# THE CENTRE IS THE AGENT TAB, NOT THE SHELL. An earlier take selected the
# shell, and a demo of a *fleet* pane then showed a bare `git:(main)` prompt:
# the pane's whole subject is agent sessions doing work, and the centre showed
# none. The pairing this pane exists for is the queue on the right and the agent
# it dispatched in the middle, so the tape selects `show the agent tab` and
# leaves the focused session's transcript in frame.
#
# IT ALSO SELECTS A SESSION, because the centre follows the session list and the
# list opens on the LEAD. The lead is the wrong half of that pairing — it is the
# session that dispatched the work, and whatever it happens to be doing is
# unrelated to the topic the queue shows RUNNING (one take caught it blocked on
# a question about a different repo entirely). `next session` steps onto a
# WORKER, so the centre is the agent whose topic the pane names. Check the
# selection before you keep a take: with a different set of sessions in flight,
# one step may land somewhere else.
#
# NOTHING IS TYPED INTO THE CENTRE once the agent tab is selected. The shell
# take cleared its pane with a `clear`; the same two lines here would deliver
# `clear` to a live agent as a PROMPT. Keep the centre read-only.
#
# THE TRANSCRIPT IS PUBLISHED, exactly like the session titles beside it — both
# are the operator's own work. Read what is actually in the centre pane before
# committing the result, not just the session list.
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
Type "next session"
Sleep 800ms
Enter
Sleep 1500ms
Ctrl+P
Sleep 800ms
Type "agent tab"
Sleep 800ms
Enter
Sleep 1500ms
Show
Sleep 1200ms
Ctrl+P
Sleep 600ms
Type "fleet queue"
Sleep 600ms
Enter
Sleep 5s
Ctrl+P
Sleep 500ms
Type "fleet queue"
Sleep 500ms
Enter
Sleep 1500ms
