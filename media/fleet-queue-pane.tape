# The demo README.md embeds. Regenerate from the checkout root with:
#
#     FLEET_QUEUE_DIR=/path/to/an/example/queue vhs media/fleet-queue-pane.tape
#
# then redact and optimise in one pass (the raw capture is roughly twice the
# size, and the Sessions column is real machine state — see below):
#
#     ffmpeg -i media/fleet-queue-pane.gif \
#       -vf "drawbox=x=18:y=47:w=228:h=587:color=0x161616:t=fill,fps=10,scale=1000:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=none" \
#       -loop 0 -y media/fleet-queue-pane.gif
#
# THE SESSIONS COLUMN IS NOT PART OF THE FIXTURE AND CANNOT BE. `FLEET_QUEUE_DIR`
# isolates the queue pane's data, but the Sessions column left of it reads this
# machine's real, running thurbox sessions — there is no equivalent env var for
# that, and thurbox has no command-palette entry for its F9 sessions-list toggle
# (`vhs` cannot send an F-key either, which is why the centre pane uses Ctrl+P
# instead of F8 below). So the `drawbox` above blanks that column's body after
# the fact, the same privacy call the centre pane makes by switching to the
# Shell tab before `Show`. The coordinates are this recording's fixed geometry
# (`Set Width`/`Set Height`/`Set Padding` below); re-measure them with the pixel
# probe in this tape's PR if any of those change.
#
# IT READS AN EXAMPLE QUEUE, not the machine's own. `queue.py` honours
# FLEET_QUEUE_DIR verbatim precisely so a harness can point at a temp directory,
# and a real control plane's queue is whatever happens to be in flight that hour
# — usually nothing worth showing, and always somebody's actual work. The
# example wants one topic per state the pane has vocabulary for: a worker that
# gave up, one running, one waiting on a recorded dependency, one with an open
# pull request, and one merged.
#
# ~107 COLUMNS ON PURPOSE. layout.lua drops the pipelines column below
# PIPELINES_MIN_COLS (120) and keeps side columns above two_panel_min_cols (80),
# so the queue pane is the only side column here without disabling a plugin or
# writing to anyone's config.
#
# EVERYTHING BEFORE `Show` IS OUT OF FRAME: thurbox booting, which is ten
# seconds of empty terminal and was most of the file size, and the centre being
# switched to the SHELL tab. The centre is otherwise a live agent transcript,
# and this recording is committed to a public repository.
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
