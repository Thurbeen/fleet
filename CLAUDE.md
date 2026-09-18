<!-- Points Claude at AGENTS.md via import; edit AGENTS.md, not this file. -->
@AGENTS.md

<!-- And at the lead's standing context, which the extension's payload could
     never deliver on its own. thurbox lays FLEET.rendered.md down under the
     extension home and symlinks CLAUDE.md, AGENTS.md and GEMINI.md at it
     there; an agent reads its context files from its CWD and that directory's
     ancestors, and the extension home is neither of those. The lead's cwd IS
     this checkout, and the renderer writes FLEET.rendered.md here, so the
     import below is what actually puts FLEET.md in front of Mission Control.

     The file is GENERATED and gitignored — `uv run fleet install-extension`
     writes it from the tracked FLEET.md, so edit FLEET.md and never the
     rendered copy. Until that command has run, or in a worktree, it is simply
     absent: a Claude Code import naming a missing file is skipped silently
     (measured on 2.1.274), so a fresh clone loads AGENTS.md alone and a worker
     on its own worktree is not handed the lead's instructions.

     tests/extension/test_lead_context.py holds all of that. -->
@FLEET.rendered.md
