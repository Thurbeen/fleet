# Vendored fonts

One file, here for one reason: **the monitor must render fully offline.**
`scripts/lib/webui.py` binds loopback and its Content-Security-Policy allows
`font-src 'self'` and nothing else, so a display font either lives in this
repo or does not exist. Fetching one from a CDN is the option that is closed.

| File | Face | Licence |
|---|---|---|
| `press-start-2p-400.woff2` | Press Start 2P, regular, latin subset | SIL OFL 1.1 — `OFL.txt` |

Press Start 2P is by Cody "CodeMan38" Boisclair
(<https://fonts.google.com/specimen/Press+Start+2P>). This is the same file the
thurbox website serves from its own origin, which is why the two surfaces look
like one product; `~/code/thurbox/website/css/variables.css` is where the rest
of the tokens come from.

The monitor asks for it only as a display face — the wordmark, the HUD counter
labels, the classification pills. Body and code stay on the system's UI and
monospace stacks, so nothing else needs vendoring and a missing font file
degrades to plain mono rather than to a blank page.
