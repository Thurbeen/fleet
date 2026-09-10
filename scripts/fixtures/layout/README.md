# `layout.lua` fixtures

What `scripts/onboarding-selftest.sh` §3 drives `scripts/place-pane.sh`
against, so that no test ever touches the operator's real arrangement.

- `stock.lua` — **recorded.** A thurbox interface's own `layout.lua`, as
  written by thurbox on first run, with fleet's queue-pane block removed. It is
  the file every new operator has, which is what makes it the right thing to
  place a column into: the `columns` list, the session column's
  `panels.shown` guard, and the `center` slot the placement is measured
  against are all thurbox's own, not this repo's idea of them.

The selftest copies it before every case and edits the copy. Nothing here is
loaded by anything at runtime; `interface/fleet_queue.lua` is the pane itself
and `scripts/lib/pane_harness.lua` is what renders it offline.

Two shapes the selftest builds inline rather than keeping here, because each is
two lines and exists only to be refused: an arrangement with no `columns` list
`place-pane.sh` recognises, and a path where no `layout.lua` exists at all.

If thurbox changes the stock arrangement, re-record this from a fresh
interface directory (`thurbox-cli plugin dir --text | head -1`) rather than
hand-editing it — a fixture nobody can regenerate stops being evidence.
