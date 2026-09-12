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
# 128 colours in ~600K; this one runs ~11s at 64 and weighs ~1.0M, and neither a
# lower framerate nor a shorter dwell buys much more — measured, 8fps saved 13K.
# Do not chase the old number by gutting the demo: TRIM DWELLS BEFORE YOU DROP
# THE F3 TOGGLE, and the toggle is the demo. The fuel block and a centre pane
# that scrolls on the toggle are what took it past the ~800K the last take
# weighed; both are things the picture is now for.
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
# WORKER, so the centre is the agent whose topic the pane names.
#
# THE NUMBER OF STEPS IS PART OF THE TAKE, NOT A CONSTANT. It was one while the
# list was flat; the workers are now drawn as CHILDREN of the lead, so one step
# lands on the first child and four land on the fourth. Whoever re-records this
# counts the steps against their own list and edits the block below — there is
# no command that selects a session by name, only `sessions.next`, so stepping
# is the only route. Two things decide the count, and both need looking at:
#
#   THE TOPIC MUST BE ONE THE PANE SHOWS RUNNING, so the two halves of the
#   frame are the same run of work. That is the requirement.
#
#   PREFER A WORKER THAT IS ACTUALLY WORKING. An earlier take of this landed on
#   an idle one whose transcript ended in a red "you've hit your session limit"
#   — honest, but it sat beside a fuel bar reading 53% and read as the pane
#   contradicting itself, in the very demo the fuel block exists for.
#
# THIS TAKE STEPS ONCE, and that is what those two rules produced against a live
# list — not a return to the flat one. Four workers were in flight: two `idle`,
# one `done`, and one that `thurbox-cli session list` called `working`, which
# happened to be the lead's first child. One step is what reaches it. Recount
# against your own list rather than inheriting this number.
#
# NOTHING IS TYPED INTO THE CENTRE once the agent tab is selected. The shell
# take cleared its pane with a `clear`; the same two lines here would deliver
# `clear` to a live agent as a PROMPT. Keep the centre read-only.
#
# THE TRANSCRIPT IS PUBLISHED, exactly like the session titles beside it — both
# are the operator's own work. Read what is actually in the centre pane before
# committing the result, not just the session list.
#
# THE PALETTE IS THURBOX'S OWN `doom` THEME, and only half of it is the tape's
# to set. The TUI paints itself from whichever theme the operator has ACTIVE —
# `active_theme` in thurbox's own state, chosen with F4 — and neither vhs nor
# this file can reach that, for the same reason the block above drives through
# the palette: the picker is an F-key. So a take carries this register only on a
# machine whose thurbox is already on `doom`. The theme name sits in the frame's
# top-right corner; read it there before keeping a take.
#
# WHAT THE `Set Theme` BELOW IS FOR is the half that IS the tape's: the terminal
# around the TUI. vhs defaults to a neutral `#171717`, which ringed a warm
# near-black interface in ten pixels of grey padding. `background` and
# `foreground` here are doom's own two values, so the padding disappears into
# the pane instead of framing it. The sixteen ANSI slots are a dark, red-accented
# set for the shell that runs before `thurbox` takes the screen; the TUI itself
# reaches none of them, because it addresses truecolour directly.
#
# DRIVEN THROUGH Ctrl+P because vhs has no F-key command and its Type sends
# escape sequences literally. The pane declares a `commands` entry, so the
# palette is a real user path; F3 is the chord a person presses.
Output media/fleet-queue-pane.gif
Set Shell bash
Set Theme { "name": "fleet doom", "background": "#180709", "foreground": "#d3cbcc", "selection": "#2e1114", "cursor": "#fc5a52", "cursorAccent": "#180709", "black": "#180709", "red": "#fc5a52", "green": "#6bbe61", "yellow": "#d99a4e", "blue": "#4ba9c0", "magenta": "#dc6761", "cyan": "#7fc7d4", "white": "#d3cbcc", "brightBlack": "#756c6d", "brightRed": "#ff8078", "brightGreen": "#8fd97f", "brightYellow": "#e5bb72", "brightBlue": "#6fc0d6", "brightMagenta": "#f08078", "brightCyan": "#9fdce6", "brightWhite": "#e5dddf" }
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
Sleep 600ms
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
