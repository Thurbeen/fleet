# The README's Kanban demo. Do not run it directly: media/fleet-board/record.sh
# seeds the throwaway profile this drives and shrinks the result.
#
# Scenes:
#   1. Mission Control, selected in the ordinary layout beside the session list
#   2. Alt+K opens the board over the layout; arrows move between cards
#   3. `n` shows only work that needs you, and `n` again shows everything
#   4. `t` searches topics; Enter keeps beacon's cards
#   5. Enter on beacon's running card focuses that worker's session
#   6. Ctrl+X returns to Mission Control, the session list still beside it
#
# The board is full-screen, so scenes 1, 5 and 6 are the layout shots that put
# the session list on either side of it.

Output fleet-board.gif

Set Shell "bash"
Set FontSize 16
Set Width 1920
Set Height 1080
Set Padding 12
Set Theme "Builtin Dark"
Set TypingSpeed 160ms
Set Framerate 10

Hide
Type `HOME="$FLEET_DEMO_HOME" exec thurbox`
Enter
Sleep 5s
# Warm the board's queue probe off camera, so it opens on records rather than
# a spinner. `l` twice folds and unfolds landed work: a key to draw on.
Alt+k
Sleep 5s
Type "l"
Sleep 300ms
Type "l"
Sleep 500ms
Escape
Sleep 1s
Show

# 1. Mission Control.
Sleep 4s

# 2. The board.
Alt+k
Sleep 3s
Right
Sleep 900ms
Down
Sleep 900ms
Down
Sleep 1200ms

# 3. Needs me, on and off.
Type "n"
Sleep 3s
Type "n"
Sleep 1200ms

# 4. Topic search.
Type "t"
Sleep 1s
Type "bea"
Sleep 1500ms
Enter
Sleep 2500ms

# 5. Beacon's running card, and its worker.
Right
Sleep 1500ms
Enter
Sleep 5s

# 6. Back to Mission Control.
Ctrl+x
Sleep 4s
