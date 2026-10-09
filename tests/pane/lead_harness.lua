-- Press Ctrl+X in the real plugin, over a session list and a rendered home.
-- arg[1] is the scenario; arg[2], when given, is a rendered lib/fleet_home.lua.
local scenario, home = arg[1], arg[2]
package.preload["lib.theme"] = function()
  return {}
end
package.preload["lib.fleet_reader"] = function()
  return dofile("interface/fleet_reader.lua")
end
if home then
  package.preload["lib.fleet_home"] = function()
    return dofile(home)
  end
end
local commands = {}
_G.command = function(name, args)
  commands[#commands + 1] = { name = name, args = args }
end
_G.state, _G.store = {}, { selected = "worker" }

-- The mark is any one short token, so none of the setting's glyphs is spelled.
local worker = { id = "worker", name = "W Fix Mission Control · then ship it", cwd = "/w" }
local unnamed = { id = "unnamed", name = "M Mission Control", cwd = "/fleet" }
local acme = { id = "acme", name = "M Mission Control · acme", cwd = "/fleet-acme" }
local remote = { id = "remote", name = "M Mission Control", cwd = "/fleet", host = "box" }
local sessions = ({
  declared = { worker, unnamed },
  ["installed-fleet"] = { worker, remote, unnamed, acme },
  ["trailing-separator"] = { worker, unnamed, acme },
  ["windows-path"] = { worker, unnamed, { id = "win", name = "M Mission Control", cwd = "C:\\fleet\\" } },
  ["one-lead"] = { worker, remote, acme },
  ["remote-only"] = { worker, remote },
  ["several-unmatched"] = { worker, unnamed, acme },
  ["no-lead"] = { worker },
})[scenario]
assert(sessions, "unknown scenario " .. tostring(scenario))
_G.thurbox = { sessions = sessions }

local plugin = dofile("interface/fleet_lead.lua")
local function press()
  assert(plugin.on_action("fleetlead.focus") == true, "the key was not consumed")
end
local function focused(id)
  press()
  assert(store.selected == id, "focused " .. tostring(store.selected) .. ", wanted " .. id)
  assert(#commands == 1 and commands[1].name == "focus" and commands[1].args.text == "agent", "agent pane not focused")
end
local function refused(words)
  press()
  assert(store.selected == "worker", "moved the selection on a guess")
  assert(#commands == 1 and commands[1].name == "message" and commands[1].args.level == "error", "said nothing")
  assert(commands[1].args.text:find(words, 1, true), commands[1].args.text)
end

if scenario == "declared" then
  local key = plugin.keys[1]
  assert(#plugin.keys == 1 and key.key == "ctrl+x" and key.action == "fleetlead.focus", "no Ctrl+X key")
  assert(key.scope == "global", "a plugin-scoped key never fires from a focused terminal")
  assert(plugin.floats and not plugin.focusable and plugin.render() == nil, "the key plugin draws or takes a slot")
  assert(not plugin.capabilities, "the key plugin asks for trust it does not need")
  assert(plugin.on_action("fleetqueue.board") == false, "consumed another plugin's action")
  focused("unnamed")
elseif scenario == "installed-fleet" or scenario == "windows-path" then
  focused(scenario == "windows-path" and "win" or "acme")
elseif scenario == "trailing-separator" then
  focused("acme")
elseif scenario == "one-lead" then
  focused("acme")
elseif scenario == "remote-only" then
  focused("remote")
elseif scenario == "several-unmatched" then
  refused("2 Mission Control sessions")
elseif scenario == "no-lead" then
  refused("no Mission Control session")
end
print("lead " .. scenario .. " passed")
