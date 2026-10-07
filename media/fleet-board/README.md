# Fleet board captures

These are synthetic records rendered by thurbox 2.51.7 with its bundled
Catppuccin light and dark themes, at 200×50 and 120×40 terminal cells.
`tests/pane/board_capture.lua` supplies the fixture to the real
`interface/fleet_board.lua` renderer. No operator records appear here.

The `.ansi.txt` files preserve the renderer’s colours and native OSC 8 links;
the plain `.txt` files preserve the full screen. PNGs are raster previews of
those ANSI captures, using local font fallback rather than a terminal emulator
screenshot. The native TUI was also exercised with mouse card selection,
pointed-column wheel scrolling, filter-chip clicks, hover and arrow keys in
both themes. Clipboard integration remains an operator manual check.
