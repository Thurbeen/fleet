-- Ctrl+X focuses Mission Control: selects the lead and puts the input on its
-- terminal, from anywhere in thurbox — a worker's focused terminal included.
--
-- A GLOBAL PLUGIN KEY IS THE WHOLE FEATURE. thurbox reads every keystroke
-- before it forwards one to a session's pty, and the only chords it ever leaves
-- to a focused terminal are `ctrl+<letter>` ones a binding marks passthrough;
-- this one is not marked, so no tmux binding, no terminal setting and nothing
-- in the agent is involved.
--
-- WHY CTRL+X AND NOT ALT+SPACE. Alt+Space was the first key, and it never
-- reaches a terminal on a Mac running the ChatGPT desktop app: that app
-- registers Option+Space as a system-wide hotkey for its launcher, and macOS
-- delivers a registered hotkey to its owner before any window sees it — `xxd`
-- in a bare Ghostty tab reads nothing for Option+Space while Option+K arrives
-- as `ESC k`. Turning the launcher shortcut off in ChatGPT's preferences
-- (`KeyboardShortcuts_toggleLauncher = false`) does not release it. Option+Space
-- is a popular global hotkey (Alfred and Raycast ship with it too), so a key
-- built on it fails silently for whoever has one of them.
--
-- The rest of the choice is what is left: a `ctrl+<letter>` chord arrives the
-- same in every terminal, inside tmux and over ssh, with no Option-as-Alt
-- setting; Ctrl+M would need the kitty keyboard protocol to differ from Enter.
-- Of the letters, Q, H and L are the kernel's; S, D, F, R, T, Z, N, O, J, K, U
-- are thurbox's own bindings; C and V are interrupt and paste; A, E and W edit
-- the line in every agent and shell; B backgrounds a command and G opens the
-- editor in Claude Code. X is free on both sides.
--
-- WHICH LEAD. The plugin files are one set per machine, so with several fleets
-- the key belongs to the one that ran `uv run fleet install-extension` LAST:
-- that run renders its checkout into `lib/fleet_home.lua`, and the key focuses
-- the local lead whose cwd it is. Never matched by name — the glyph is a
-- setting and a named fleet's lead carries `· <name>`. `fleet_reader.lead_for`
-- owns the rule, including the one-lead fallback when nothing was rendered.
--
-- Draws nothing and needs no layout slot, no capability and no trust.
local reader = require("lib.fleet_reader")
local rendered, home = pcall(require, "lib.fleet_home")
local checkout = rendered and type(home) == "table" and home.checkout or nil
return {
  name = "fleetlead",
  floats = true,
  order = 93,
  focusable = false,
  keys = {
    {
      key = "ctrl+x",
      action = "fleetlead.focus",
      desc = "focus Mission Control",
      scope = "global",
      group = "UI",
    },
  },
  commands = { { action = "fleetlead.focus", desc = "focus Mission Control", group = "UI" } },
  render = function()
    return nil
  end,
  on_action = function(action)
    if action ~= "fleetlead.focus" then
      return false
    end
    local lead, why = reader.lead_for(thurbox.sessions, checkout)
    if lead then
      -- What the session list itself does to open a row it did not have to
      -- find: `selected` is the store key that steers its cursor, and the agent
      -- pane is what shows a session. Not `command("action", { text =
      -- "session.focus" })`: from a plugin that kernel action arrives without
      -- its session argument and does nothing.
      store.selected = lead.id
      command("focus", { text = "agent" })
    else
      command("message", { text = why, level = "error" })
    end
    return true
  end,
}
