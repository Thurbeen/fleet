# Fleet board captures

These are synthetic records rendered by thurbox 2.51.7 with its bundled
Catppuccin light and dark themes, at 200×50 and 120×40 terminal cells.
`tests/pane/board_capture.lua` supplies the fixture to the real
`interface/fleet_board.lua` renderer. No operator records appear here.

The `.ansi.txt` files preserve the renderer’s colours and native OSC 8 links;
the plain `.txt` files preserve the full screen. PNGs are raster previews of
those ANSI captures, using local font fallback rather than a terminal emulator
screenshot. The native TUI was also exercised with mouse card selection,
selected-column wheel scrolling, filter-chip clicks, hover and arrow keys in
both themes, including topic search/selection/cancel/clear. Default board
captures hide fuel; `-fuel` captures show its compact two-column gauges, and
`-topics` captures show search inside the board’s detail area. Native
drag-selection was also exercised and is blocked by thurbox 2.51.7’s float
input routing; the board cannot enable copying itself.

The README’s `../fleet-board.gif` is a recording of a real thurbox TUI in the
**Doom** theme, at 1920×1080. `record.sh` builds it in a throwaway profile:
its own HOME, thurbox config and data, and tmux server. That profile holds a
copy of this checkout with three synthetic topics and four sessions. Their
stand-in agent, `demo-agent.sh`, prints a transcript from `transcripts/`. The
plugins and the queue probe are this checkout's own, and no operator records
appear. `fleet-board.tape` lists the scenes: Mission Control beside the
session list, the board with navigation, the needs-me filter and topic search,
a worker opened from its card, and Ctrl+X back to Mission Control. To
re-record, run `media/fleet-board/record.sh`. It needs thurbox, uv, git,
sqlite3, flock, vhs and ffmpeg, on Linux.
