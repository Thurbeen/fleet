-- Alt+K Kanban overlay. It returns nothing while closed and needs no layout slot.
local reader = require("lib.fleet_reader")
local board = require("lib.fleet_board")
local ui = require("lib.ui")
local theme = require("lib.theme")
local last_reading
return {
  name = "fleetkanban",
  floats = true,
  order = 90,
  focusable = false,
  capabilities = { "run" },
  keys = {
    {
      key = "alt+k",
      action = "fleetqueue.board",
      desc = "toggle the fleet Kanban board",
      scope = "global",
      group = "UI",
    },
    { key = "up", action = "fleetqueue.board_up", desc = "previous card", group = "Kanban" },
    { key = "down", action = "fleetqueue.board_down", desc = "next card", group = "Kanban" },
    { key = "left", action = "fleetqueue.board_left", desc = "previous column", group = "Kanban" },
    { key = "right", action = "fleetqueue.board_right", desc = "next column", group = "Kanban" },
    { key = "enter", action = "fleetqueue.board_enter", desc = "focus worker or show record", group = "Kanban" },
    { key = "esc", action = "fleetqueue.board_close", desc = "close board or detail", group = "Kanban" },
    { key = "f", action = "fleetqueue.board_fuel", desc = "show or hide fuel gauges", group = "Kanban" },
    { key = "t", action = "fleetqueue.board_topic", desc = "search and choose a topic", group = "Kanban" },
    { key = "a", action = "fleetqueue.board_agent", desc = "cycle agent filter", group = "Kanban" },
    { key = "n", action = "fleetqueue.board_needs", desc = "toggle needs me filter", group = "Kanban" },
    { key = "l", action = "fleetqueue.board_landed", desc = "fold recent landed cards", group = "Kanban" },
    { key = "d", action = "fleetqueue.board_detail", desc = "show full queue record", group = "Kanban" },
  },
  commands = { { action = "fleetqueue.board_topic_all", desc = "show all topics", group = "Kanban" } },
  render = function(ctx)
    if not state.board_open then
      return nil
    end
    local reading = reader.read(ctx)
    if
      reading.error
      and last_reading
      and reading.lead
      and reading.lead.id == last_reading.lead.id
      and reading.lead.cwd == last_reading.lead.cwd
    then
      return board.render(
        ctx,
        last_reading.model,
        last_reading.fuel,
        last_reading.lead,
        last_reading.fleet,
        "queue unavailable · showing last records"
      )
    end
    if reading.error then
      board.invalidate()
      local node = ui.panel({
        title = "Fleet board · " .. ui.chord("fleetqueue.board"),
        body = {
          { type = "text", wrap = true, fill = 1, text = table.concat(reading.error, "\n") },
          ui.footer({ cancel = "Close", actions = { { "fleetqueue.board_close", "close" } } }),
        },
      })
      node.float = { width = 100, height = 100 }
      node.frame.style = { fg = theme.text, bg = theme.role("app_bg") }
      return node
    end
    last_reading = reading
    return board.render(ctx, reading.model, reading.fuel, reading.lead, reading.fleet)
  end,
  on_action = function(action)
    if action == "fleetqueue.board" then
      state.board_open = not state.board_open
      state.board_detail, state.board_picker = nil, nil
      return true
    end
    return state.board_open and board.on_action(action) or false
  end,
  on_key = function(key)
    return state.board_open and board.on_key(key) or false
  end,
  on_click = function(hit)
    return state.board_open and board.on_click(hit) or false
  end,
  on_scroll = function(wheel)
    return state.board_open and board.on_scroll(wheel) or false
  end,
}
