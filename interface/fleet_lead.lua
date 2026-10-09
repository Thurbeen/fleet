-- Alt+Space focuses Mission Control: selects the lead and puts the input on its
-- terminal, from anywhere in thurbox — a worker's focused terminal included.
--
-- A GLOBAL PLUGIN KEY IS THE WHOLE FEATURE. thurbox reads every keystroke
-- before it forwards one to a session's pty, and the only chords it ever leaves
-- to a focused terminal are `ctrl+<letter>` ones a binding marks passthrough;
-- `alt+space` is neither, so no tmux binding, no terminal setting and nothing
-- in the agent is involved. The terminal must send Option as Alt: Ghostty does
-- on a U.S. layout by default, and elsewhere `macos-option-as-alt = true` (or
-- the emulator's equivalent) is what makes every Alt chord here work, Alt+K's
-- included.
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
      key = "alt+space",
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
