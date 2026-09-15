# `layout.lua` fixtures

What `tests/pane/test_place_pane.py` drives `uv run fleet place-pane` against,
so that no test ever touches the operator's real arrangement.

- `stock.lua` — **recorded.** A thurbox interface's own `layout.lua`, as
  written by thurbox on first run, with fleet's queue-pane block removed. It is
  the file every new operator has, which is what makes it the right thing to
  place a column into: the `columns` list, the session column's
  `panels.shown` guard, and the `center` slot the placement is measured
  against are all thurbox's own, not this repo's idea of them.

The tests copy it before every case and edit the copy. Nothing here is loaded
by anything at runtime; `interface/fleet_queue.lua` is the pane itself and
`scripts/lib/pane_harness.lua` is what renders it offline.

Four shapes the tests build inline rather than keeping here, because each is a
few lines or a transform of `stock.lua`: an arrangement with no `columns` list
`fleet place-pane` recognises, one that carries the `center` anchor but none of
the helpers the block calls, and a path where no `layout.lua` exists at all —
each only there to be refused — plus `stock.lua` with CRLF line endings, which
must come back CRLF.

If thurbox changes the stock arrangement, re-record this from a fresh
interface directory (the first line of `thurbox-cli plugin dir --text`) rather
than hand-editing it — a fixture nobody can regenerate stops being evidence.
