-- Drive the real pane over probe records, with the kernel's node and action seams.
local scenario = arg[1]
local function len(s)
  return utf8.len(s or "") or 0
end
local function cut(s, w)
  local out = {}
  for _, c in utf8.codes(s or "") do
    if #out >= w then
      break
    end
    out[#out + 1] = utf8.char(c)
  end
  return table.concat(out)
end
local theme = setmetatable({
  role = function(n)
    return n
  end,
  spinner_frame = function()
    return "◐"
  end,
  status = function(s)
    return {
      glyph = ({ working = "◐", idle = "○", blocked = "◆", done = "●", unreported = "◌", error = "✗" })[s]
        or "◌",
      color = "status_" .. s,
    }
  end,
}, {
  __index = function(_, k)
    return k
  end,
})
local widgets = {
  len = len,
  truncate = function(s, w)
    return len(s) > w and cut(s, math.max(0, w - 1)) .. "…" or s
  end,
  time_ago = function()
    return "50m ago"
  end,
  now_ms = function()
    return 2000000000000
  end,
  pad = function(s, w)
    return s .. string.rep(" ", math.max(0, w - len(s)))
  end,
}
widgets.keep_left = cut
widgets.truncate_hard = cut
widgets.chars = function(s)
  return len(s)
end
widgets.divider = function()
  return { type = "text", len = 1, text = { {} } }
end
widgets.gauge = function(r, o)
  return {
    type = "text",
    len = 1,
    text = {
      {
        { text = string.rep("█", math.floor((o.width or 10) * r)), style = o.style },
        { text = string.rep("░", (o.width or 10) - math.floor((o.width or 10) * r)), style = { fg = "muted" } },
        { text = " " .. (o.label or "") },
      },
    },
  }
end
local Row = {}
Row.__index = Row
function Row:add(s, style)
  self.spans[#self.spans + 1] = { text = s, style = self.tone and self.tone(style) or style }
  self.used = self.used + len(s)
  return self
end
function Row:trailing(s, style)
  return self:add(" " .. widgets.truncate(s, self.width - self.used - 1), style)
end
function Row:spans_list()
  return self.spans
end
local ui = {
  row = function(o)
    return setmetatable({ width = o.width or 200, used = 0, spans = {}, tone = o.tone }, Row)
  end,
  status = function(s)
    return theme.status(s)
  end,
  chord = function(a)
    return a
  end,
}
function ui.panel(o)
  return {
    type = "box",
    children = o.body.type and { o.body } or o.body,
    frame = {
      title = { { text = o.title } },
      border_type = o.focused and "thick" or "rounded",
      overlay = { right_column = o.right_column },
    },
  }
end
function ui.list(o)
  local c = {}
  for i = 1, math.min(#o.items, o.height) do
    local item = o.items[i]
    local selected = i == o.cursor
    c[#c + 1] = {
      type = "text",
      len = 1,
      id = o.id_of and o.id_of(item),
      class = selected and "selected" or "row",
      style = selected and { bg = "selection_bg", fg = "selection_fg" },
      text = { o.row(item, selected) },
    }
  end
  return { type = "box", children = c }
end
function ui.footer(o)
  return { type = "text", len = 1, text = { { { text = "close" } } }, actions = o.actions }
end
function ui.modal(o)
  local n = ui.panel({ title = o.title, body = o.children })
  n.float = { width = 100, height = 100 }
  return n
end
package.preload["lib.widgets"] = function()
  return widgets
end
package.preload["lib.theme"] = function()
  return theme
end
package.preload["lib.ui"] = function()
  return ui
end
package.preload["lib.hover"] = function()
  return {
    role = function()
      return false
    end,
    id = function(id)
      return thurbox.hover and thurbox.hover.id == id
    end,
    style = function(_, _, base)
      return base
    end,
    button_style = function()
      return { bg = "accent" }
    end,
  }
end
package.preload["lib.panels"] = function()
  return {
    toggle = function() end,
    shown = function()
      return true
    end,
  }
end
package.preload["lib.scroll"] = function()
  return {
    window_variable = function(heights, offset, selected, height)
      local visible = math.max(1, math.floor(height / 5))
      local first = math.max(1, math.min(offset + 1, selected))
      if selected >= first + visible then
        first = selected - visible + 1
      end
      return first, math.min(visible, #heights - first + 1)
    end,
  }
end
package.preload["lib.textinput"] = function()
  return {
    new = function(value)
      return { value = value, cursor = len(value) }
    end,
    node = function(field, opts)
      return { type = "input", len = 3, value = field.value, focused = opts.focused, id = opts.id }
    end,
    key = function(field, key)
      if key.key == "backspace" then
        field.value = field.value:sub(1, -2)
      elseif key.char and not key.ctrl and not key.alt then
        field.value = field.value .. key.char
      elseif key.key == "left" or key.key == "right" then
        return true
      else
        return false
      end
      field.cursor = len(field.value)
      return true
    end,
  }
end
package.preload["lib.fuzzy"] = function()
  return {
    compile = function(q)
      return q:lower()
    end,
    match = function(q, value)
      local pos = 1
      for c in q:gmatch(".") do
        local at = value:lower():find(c, pos, true)
        if not at then
          return nil
        end
        pos = at + 1
      end
      return {}
    end,
  }
end
package.preload["lib.fleet_reader"] = function() return dofile("interface/fleet_reader.lua") end
package.preload["lib.fleet_board"] = function()
  return dofile("interface/fleet_board.lua")
end
_G.state = {}
_G.store = {}
_G.thurbox = {
  taken_at_ms = 2000000000000,
  theme = { nerd_font = false },
  sessions = { { id = "lead", name = "Mission Control", cwd = "/fleet" }, { id = "worker", status = "working" } },
  runs = {},
}
local records = { "R\t/queue", "H\tticking\t2000000000", "T\talpha\tAlpha" }
local function task(id, state, title, session, agent, block, method, publish)
  records[#records + 1] = table.concat({
    "K",
    id,
    state,
    title,
    "",
    "https://github.com/example/repo/pull/2",
    block or "",
    "1",
    "0",
    "0",
    "feat/work",
    "1999999000",
    method or "pr",
    publish or "",
    "1999999990",
  }, "\t")
  records[#records + 1] = table.concat({
    "B",
    "alpha/" .. id,
    agent or "codex",
    "remote",
    session or "",
    "https://example.org/review",
    publish == "checks-failed" and "failed checks: unit" or "",
    publish == "checks-failed" and "2" or "",
  }, "\t")
end
task("01-ready", "queued", "Ready")
task("02-wait", "queued", "Waiting", nil, "claude", "!approval required|awaiting-approval")
task("03-work", "dispatched", "Working", "worker")
task("04-pr", "done", "Failing PR", nil, nil, nil, "pr", "checks-failed")
task("05-served", "done", "Reader", nil, nil, nil, "served", "served")
task("06-stuck", "stuck", "Stuck")
task("07-landed", "landed", "Recent")
if scenario == "planned-served" then
  task("08-planned", "queued", "Planned page", nil, nil, nil, "served")
elseif scenario == "landed-age" then
  task("08-old", "landed", "Old landing")
  records[#records - 1] = records[#records - 1]:gsub("1999999000", "1999900000")
  task("09-undated", "landed", "Undated landing")
  records[#records - 1] = records[#records - 1]:gsub("1999999000", "0")
end
if scenario == "topic-picker" then
  for i = 1, 35 do
    records[#records + 1] = "T\ttopic-"
      .. i
      .. "\t"
      .. (i == 23 and "Observability rollout" or i == 1 and "topic-2 migration" or "Project " .. i)
    task("01-work", "queued", "Picker task " .. i)
  end
end
if scenario == "large" or scenario == "independent-probes" then
  for i = 1, scenario == "large" and 5000 or 500 do
    task("many-" .. i, "queued", "Large " .. i)
  end
end
thurbox.runs["fleetqueue:lead"] = { state = "done", stdout = table.concat(records, "\n") .. "\n" }
thurbox.runs["fleetfuel:lead"] = {
  state = "done",
  stdout = "provider\tfixture\nremaining\t62\nreserve\t20\nwindow\tsession\t62\t2000003000\tsession\n\n",
}
-- Three peers on the board's wire: one read, one never reached, and one whose
-- last reading is stale — and whose topic shares a slug with this fleet's, so
-- two `alpha/01-ready` cards must stay two cards.
local peer_wire = table.concat({
  "P\tdevbox:/srv/fleet\tdevbox\tdevbox/fleet\tok\t0\t",
  "R\t/srv/fleet/orchestration/queue",
  "H\tticking\t2000000000",
  "T\tfar\tFar topic",
  "K\t01-far\tdispatched\tFar away task\t\t\t\t1\t0\t0\tfeat/far\t1999999000\tpr\t\t0",
  "B\tfar/01-far\tclaude\t\tworker\t\t\t",
  "A\t0",
  "P\tdownbox:/srv/fleet\tdownbox\tdownbox/fleet\tunreachable\t180\tNo route to host",
  "P\tlocal:/other\tlocal\tlocal/acme\tstale\t600\tno answer in 15s",
  "R\t/other/orchestration/queue",
  "T\talpha\tAlpha elsewhere",
  "K\t01-ready\tqueued\tElsewhere ready\t\t\t\t1\t0\t0\tfeat/x\t1999999000\tpr\t\t0",
  "B\talpha/01-ready\tcodex\t\t\t\t\t",
  "A\t0",
}, "\n") .. "\n"
if scenario:match("^peers") and scenario ~= "peers-none" then
  thurbox.runs["fleetpeers:lead"] = { state = "done", stdout = peer_wire }
end
local calls, commands = {}, {}
_G.run = function(key, cmd, opts)
  calls[#calls + 1] = { key = key, cmd = cmd, opts = opts }
end
_G.command = function(name, args)
  commands[#commands + 1] = { name = name, args = args }
end
local column = dofile("interface/fleet_queue.lua")
local pane = dofile("interface/fleet_kanban.lua")
if scenario == "closed-float" then
  for _ = 1, 20 do
    assert(pane.render({ width = 200, height = 50, elapsed = 0 }) == nil, "closed float rendered a column")
  end
  assert(#calls == 0, "closed board requested queue or fuel probes")
  print("closed-float passed: 20 frames, zero trees, zero probes")
  return
elseif scenario == "float-contract" then
  assert(not column.floats, "a layout column cannot bypass native occupied_slots as a float")
  local floating = dofile("interface/fleet_kanban.lua")
  assert(floating.floats, "the board must own its floating surface")
  assert(floating.render({ width = 200, height = 50, elapsed = 0 }) == nil, "closed float rendered a column")
  assert(#calls == 0, "closed board requested queue or fuel probes")
  assert(floating.on_action("fleetqueue.board"))
  assert(floating.render({ width = 200, height = 50, elapsed = 0 }).float, "open board is not floating")
  assert(column.on_action("fleetqueue.board") == false, "column still owns the board toggle")
  print("float-contract passed")
  return
elseif scenario == "independent-probes" then
  local reader = require("lib.fleet_reader")
  local read = reader.read
  local phase, readings = nil, {}
  reader.read = function(...)
    local reading = read(...)
    readings[phase] = reading
    return reading
  end
  local ctx = { width = 200, height = 50, elapsed = 0 }
  local column_state, board_state = {}, { board_open = true }
  local original = thurbox.runs["fleetqueue:lead"].stdout
  local updated = original:gsub("Ready", "Updated task")
  local original_fuel = thurbox.runs["fleetfuel:lead"].stdout
  local updated_fuel = original_fuel:gsub("remaining\t62", "remaining\t61")
  local first = {}
  for frame = 1, 20 do
    for _, view in ipairs({ "column", "board" }) do
      phase = view
      state = view == "column" and column_state or board_state
      thurbox.runs["fleetqueue:lead"].stdout = view == "column" and original or updated
      thurbox.runs["fleetfuel:lead"].stdout = view == "column" and original_fuel or updated_fuel
      local node = (view == "column" and column or pane).render(ctx)
      assert(node, view .. " stopped drawing")
      local reading = readings[view]
      assert(reading and reading.model, view .. " stopped reading records")
      if frame == 1 then
        first[view] = reading
      else
        assert(reading.model == first[view].model, view .. " reparsed its unchanged probe answer")
        assert(reading.fuel == first[view].fuel, view .. " reparsed its unchanged fuel answer")
      end
    end
  end
  assert(first.column.model ~= first.board.model, "different snapshots shared stale task tables")
  assert(first.column.fuel[1].remaining == 62 and first.board.fuel[1].remaining == 61, "fuel snapshots crossed views")
  phase, state = "board", board_state
  thurbox.runs["fleetqueue:lead"].stdout = updated:gsub("H\tticking\t2000000000", "H\tbehind\t2000000010")
  pane.render(ctx)
  assert(readings.board.model == first.board.model, "health-only update rebuilt board records")
  assert(readings.board.model.health == "behind", "board health stopped refreshing")
  assert(first.column.model.health == "ticking", "board health leaked into column snapshot")
  thurbox.runs["fleetqueue:lead"].stdout = updated:gsub("Updated task", "Newest task")
  pane.render(ctx)
  assert(readings.board.model ~= first.board.model, "board reused outdated tasks")
  phase, state = "column", column_state
  thurbox.runs["fleetqueue:lead"].stdout = original
  thurbox.runs["fleetfuel:lead"].stdout = original_fuel
  column.render(ctx)
  assert(readings.column.model == first.column.model, "board refresh evicted the column model")
  assert(readings.column.fuel == first.column.fuel, "board refresh evicted the column fuel")
  print("independent-probes passed: 20 alternating frames preserve both snapshots")
  return
elseif scenario == "health-memo" then
  local model_for
  if package.preload["lib.fleet_reader"] then
    model_for = require("lib.fleet_reader").model_for
  else
    for i = 1, 80 do
      local name, value = debug.getupvalue(pane.render, i)
      if name == "model_for" then model_for = value; break end
    end
  end
  assert(model_for, "record reader unavailable")
  local original = thurbox.runs["fleetqueue:lead"].stdout
  local before = model_for(original)
  local refreshed = model_for(original:gsub("H\tticking\t2000000000", "H\tbehind\t2000000010"))
  assert(before == refreshed, "a health epoch rebuilt unchanged task tables")
  assert(refreshed.health == "behind" and refreshed.read_at == 2000000010, "cached header stopped refreshing")
  local changed = model_for(original:gsub("Ready", "Changed task"))
  assert(changed ~= before, "changed task records reused stale data")
  print("health-memo passed")
  return
end
assert(pane.on_action("fleetqueue.board"), "the live pane has no board toggle")
local ctx = {
  width = (scenario == "narrow" or scenario == "mouse-band" or scenario:match("%-narrow$")) and 120 or 200,
  height = (scenario == "narrow" or scenario == "mouse-band" or scenario:match("%-narrow$")) and 40 or 50,
  elapsed = 0,
}
local function tree()
  return pane.render(ctx)
end
local function nodes(root, out)
  out = out or {}
  out[#out + 1] = root
  for _, child in ipairs(root.children or {}) do
    nodes(child, out)
  end
  return out
end
local function strings(root)
  local out = {}
  for _, node in ipairs(nodes(root)) do
    local value = node.text
    if type(value) == "string" then
      out[#out + 1] = value
    elseif type(value) == "table" then
      for _, line in ipairs(value) do
        if line.text then
          out[#out + 1] = line.text
        else
          for _, span in ipairs(line) do
            out[#out + 1] = span.text or ""
          end
        end
      end
    end
    if node.frame then
      for _, s in ipairs(node.frame.title or {}) do
        out[#out + 1] = s.text
      end
    end
  end
  return table.concat(out, " ")
end
local function contains(s)
  assert(strings(tree()):find(s, 1, true), s .. " missing")
end
local function action(a)
  assert(pane.on_action("fleetqueue.board_" .. a), a .. " not handled")
  if state.board_open then
    tree()
  end
end
if scenario == "render-readonly" then
  local function readonly(value)
    if type(value) ~= "table" then return value end
    return setmetatable({}, {
      __index = function(_, k) return readonly(value[k]) end,
      __newindex = function(_, k) error("render wrote state: " .. k) end,
      __len = function() return #value end,
      __pairs = function() return pairs(value) end,
    })
  end
  local writable = state
  state = readonly(writable)
  tree() -- First render, not just a warmed-up idle frame.
  for _ = 1, 10 do tree() end
  state = writable
  action("topic")
  state = readonly(writable)
  for _ = 1, 10 do tree() end
  state = writable
  action("close")
  action("detail")
  state = readonly(writable)
  for _ = 1, 10 do tree() end
  print("render-readonly passed: board, topic search and record write no state")
  return
end
tree()
if scenario == "changed-fleet" then
  thurbox.sessions[#thurbox.sessions + 1] = { id = "other", name = "Mission Control", cwd = "/other-fleet" }
  store.selected = "other"
  contains("reading the queue")
  assert(not strings(tree()):find("Ready", 1, true), "a failed new fleet probe displayed another fleet's records")
elseif scenario == "retain-search" then
  action("topic")
  assert(pane.on_key({ key = "a", char = "a" }))
  local picker = state.board_picker
  thurbox.runs["fleetqueue:lead"] = { state = "failed", stdout = "" }
  contains("Choose topic")
  assert(state.board_picker == picker and picker.field.value == "a", "failed probe cleared topic search")
  contains("queue unavailable")
elseif scenario == "retain-detail" then
  action("detail")
  local ref = state.board_detail
  thurbox.runs["fleetqueue:lead"] = { state = "failed", stdout = "" }
  contains("Detail")
  assert(state.board_detail == ref, "failed probe closed the record")
  contains("queue unavailable")
elseif scenario == "columns" then
  for _, s in ipairs({ "Queued / waiting", "Dispatched / working", "Shipped", "Served", "Stuck / abandoned", "Landed" }) do
    contains(s)
  end
elseif scenario == "enter-session" then
  pane.on_click({ id = "board:alpha/03-work" })
  action("enter")
  assert(
    commands[#commands].name == "action"
      and commands[#commands].args.text == "session.focus"
      and commands[#commands].args.session == "worker"
  )
  assert(not state.board_open)
elseif scenario == "enter-detail" then
  action("enter")
  contains("Detail")
  local found = false
  for _, call in ipairs(calls) do
    if call.cmd:find("queue show", 1, true) then
      found = true
    end
  end
  assert(found, "Enter never requests the record")
elseif scenario == "links" then
  local found = {}
  for _, n in ipairs(nodes(tree())) do
    if n.role then
      found[n.role] = true
    end
    for _, line in ipairs(type(n.text) == "table" and n.text or {}) do
      for _, s in ipairs(line.text and { line } or line) do
        if s.role then
          found[s.role] = true
        end
      end
    end
  end
  assert(
    found["url:https://github.com/example/repo/pull/2"] and found["url:https://example.org/review"],
    "native link roles missing"
  )
elseif scenario == "filters" then
  action("needs")
  assert(not strings(tree()):find("Ready", 1, true))
  contains("Failing PR")
  contains("Reader")
  action("needs")
  action("agent")
  assert(not strings(tree()):find("Waiting", 1, true))
  action("agent")
  contains("Waiting")
elseif scenario == "topic-context" then
  action("topic")
  contains("Choose topic")
  contains("Fleet board")
  for _, heading in ipairs({
    "Queued / waiting",
    "Dispatched / working",
    "Shipped",
    "Served",
    "Stuck / abandoned",
    "Landed",
  }) do
    contains(heading)
  end
  contains("Ready")
  for _, run in ipairs(tree().children[3].text[1]) do
    assert(
      run.role ~= "action:fleetqueue.board_agent" and run.role ~= "action:fleetqueue.board_fuel",
      "background filter chips still act as typing while choosing a topic"
    )
  end
  assert(state.board_topic == nil, "opening the picker filtered the background")
  for _, node in ipairs(nodes(tree())) do
    for _, line in ipairs(type(node.text) == "table" and node.text or {}) do
      for _, run in ipairs(line) do
        assert(run.role ~= "action:fleetqueue.board_landed", "background landed button types into search")
      end
    end
  end
  assert(pane.on_click({ id = "board:alpha/03-work" }), "background cards became dead controls")
  assert(
    not state.board_picker and state.board_ref == "alpha/03-work",
    "clicking a card did not dismiss search and select it"
  )
  action("topic")
  action("close")
  local advertised = false
  for _, item in ipairs(tree().children[#tree().children].actions or {}) do
    advertised = advertised or item[1] == "fleetqueue.board_topic" and item[2] == "topic"
  end
  assert(advertised, "topic shortcut is missing from the board footer description")
elseif scenario == "topic-picker" then
  action("topic")
  contains("Choose topic")
  assert(state.board_topic == nil, "opening the picker changed the filter")
  contains("All topics")
  local input = false
  for _, node in ipairs(nodes(tree())) do
    input = input or node.type == "input" and node.focused == true
  end
  assert(input, "picker has no native search input")
  local measured = false
  for _, node in ipairs(nodes(tree())) do
    if node.children and node.children[1] and (node.children[1].id or ""):find("board-topic:", 1, true) then
      measured = (node.len or 0) > 0
    end
  end
  assert(measured, "picker list has no measured height in the board")
  for char in ("rollout"):gmatch(".") do
    if char == "t" then
      action("topic")
    elseif char == "l" then
      action("landed")
    else
      assert(pane.on_key({ key = char, char = char }))
    end
  end
  contains("Observability rollout")
  assert(not strings(tree()):find("Project 1", 1, true), "search did not narrow topics")
  action("enter")
  assert(state.board_topic == "topic-23", "Enter did not jump directly to the matching topic")
  contains("Picker task 23")
  assert(not strings(tree()):find("Picker task 22", 1, true))
  action("topic")
  assert(pane.on_key({ key = "z", char = "z" }))
  contains("No matching topics")
  action("enter")
  contains("Choose topic")
  action("close")
  assert(state.board_topic == "topic-23" and state.board_open, "cancel lost the filter or closed the board")
  action("topic")
  assert(pane.on_click({ id = "board-topic:topic-35" }))
  assert(state.board_topic == "topic-35", "click did not choose its topic")
  action("topic_all")
  assert(state.board_topic == nil, "All topics did not clear the filter")
  action("topic")
  for char in ("topic-2"):gmatch(".") do
    assert(pane.on_key({ key = char, char = char }))
  end
  action("enter")
  assert(state.board_topic == "topic-2", "an exact slug match was ranked below another topic's title")
  action("topic")
  action("topic_all")
  action("topic")
  action("down")
  assert(pane.on_scroll({ up = false }))
  action("enter")
  assert(state.board_topic == "topic-1", "arrow and wheel navigation did not select the listed row")
elseif scenario == "landed" then
  assert(not strings(tree()):find("Recent", 1, true))
  action("landed")
  contains("Recent")
elseif scenario == "mouse" then
  pane.on_click({ id = "board:alpha/04-pr" })
  contains("Failing PR")
  pane.on_click({ id = "board:alpha/02-wait" })
  assert(pane.on_scroll({ x = 90, y = 10, up = true }))
  assert(state.board_ref == "alpha/01-ready", "native float wheel must move the selected column")
elseif scenario == "detail-links" then
  action("enter")
  local link = "https://example.org/reviews/" .. string.rep("x", 260)
  thurbox.runs["fleetboard-detail:lead:alpha/01-ready"] =
    { state = "done", stdout = "review: " .. link .. "\n" .. string.rep("line\n", 80) }
  local found = false
  for _, n in ipairs(nodes(tree())) do
    for _, line in ipairs(type(n.text) == "table" and n.text or {}) do
      for _, s in ipairs(line.text and { line } or line) do
        if s.role == "url:" .. link then
          found = true
        end
      end
    end
  end
  assert(found, "wrapping broke the full review URL")
  assert(pane.on_scroll({ x = 1, y = 5, up = false }))
  assert(state.board_detail_offset == 1)
elseif scenario == "selection" then
  action("enter")
  assert(pane.on_key({ key = "ctrl+c" }) == false, "copy key swallowed")
  for _, n in ipairs(nodes(tree())) do
    assert(n.role ~= "drag", "text selection stolen by pane drag")
  end
elseif scenario == "planned-served" then
  action("needs")
  assert(not strings(tree()):find("Planned page", 1, true), "a planned document is not awaiting a reader")
  contains("Reader")
elseif scenario == "landed-age" then
  action("landed")
  contains("Recent")
  assert(not strings(tree()):find("Old landing", 1, true))
  assert(not strings(tree()):find("Undated landing", 1, true), "undated work is not known to be recent")
elseif scenario == "mouse-band" then
  pane.on_click({ id = "board:alpha/05-served" })
  local root = tree()
  local second_band_y = 1
    + root.children[1].len
    + root.children[2].len
    + root.children[3].len
    + root.children[4].children[1].len
  assert(pane.on_scroll({ x = 5, y = second_band_y, up = false }))
  assert(state.board_ref == "alpha/05-served", "native wheel left the selected lower-band column")
elseif scenario == "glyphs" then
  local ready = false
  for _, node in ipairs(nodes(tree())) do
    if node.id == "board:alpha/01-ready" and strings(node):find("Ready", 1, true) then
      ready = strings(node):find("○", 1, true) ~= nil
    end
  end
  assert(ready, "a ready queued card wears the blocked glyph")
elseif scenario == "fuel-compact" then
  thurbox.runs["fleetfuel:lead"].stdout =
    "provider\tfixture\nreserve\t20\nwindow\t5h\t62\t2000003000\nwindow\t7d\t40\t2000003000\n\nprovider\tother\nreserve\t20\nwindow\t5h\t15\t2000003000\nwindow\t7d\t73\t2000003000\n\n"
  action("fuel")
  contains("62%")
  contains("15%")
  local fuel = tree().children[2]
  assert(fuel.len == 2 and #fuel.children == 2, "four gauges should occupy two rows")
  assert(#fuel.children[1].children == 2, "fuel is not arranged in two columns")
  for _, row in ipairs(fuel.children) do
    for _, cell in ipairs(row.children) do
      local label, gauge = cell.children[1], cell.children[2]
      local used = label.len
      for _, span in ipairs(gauge.text[1]) do
        used = used + len(span.text)
      end
      assert(used <= math.floor((ctx.width - 3) / 2), "fuel percentage extends beyond its column")
    end
  end
elseif scenario == "fuel-default" then
  assert(not strings(tree()):find("62%%"), "fuel gauges dominate the board by default")
  contains("fuel: hidden")
  action("fuel")
  contains("62%")
  action("fuel")
  assert(not strings(tree()):find("62%%"), "fuel toggle did not hide gauges again")
elseif scenario == "fuel" then
  action("fuel")
  contains("62%")
elseif scenario == "narrow" then
  assert(tree().float)
  assert(#nodes(tree()) < 250)
elseif scenario == "large" then
  assert(#nodes(tree()) < 300, "built nodes for the entire queue")
elseif scenario == "fuel-unavailable" then
  action("fuel")
  thurbox.runs["fleetfuel:lead"] = { state = "done", stdout = "provider\tfixture\nunavailable\tno credentials\n\n" }
  contains("unavailable")
  assert(tree().children[2].children[1].children[1].len == nil, "unavailable fuel was clipped to one column")
elseif scenario == "buttons" then
  local found = {}
  for _, n in ipairs(nodes(tree())) do
    for _, line in ipairs(type(n.text) == "table" and n.text or {}) do
      for _, s in ipairs(line.text and { line } or line) do
        if s.role then
          found[s.role] = true
        end
      end
    end
  end
  for _, a in ipairs({ "topic", "agent", "needs", "landed", "fuel", "detail" }) do
    assert(found["action:fleetqueue.board_" .. a], "button lacks native hover/click role: " .. a)
  end
  action("topic")
  pane.on_click({ id = "board-topic:alpha" })
  contains("topic: alpha")
  action("topic")
  action("topic_all")
  contains("topic: all")
elseif scenario == "missing-session" then
  pane.on_click({ id = "board:alpha/03-work" })
  thurbox.sessions[2] = nil
  action("enter")
  contains("Detail")
elseif scenario == "reserved" then
  assert(not pane.on_key({ key = "ctrl+h" }) and not pane.on_key({ key = "ctrl+l" }))
elseif scenario == "peers-none" then
  -- No peer is the board as it always was: no answer, an empty answer and a
  -- probe that failed all draw the identical tree, chip for chip.
  local function dump(value, out)
    out = out or {}
    if type(value) ~= "table" then
      out[#out + 1] = tostring(value)
      return out
    end
    local keys = {}
    for k in pairs(value) do
      keys[#keys + 1] = k
    end
    table.sort(keys, function(a, b)
      return tostring(a) < tostring(b)
    end)
    out[#out + 1] = "{"
    for _, k in ipairs(keys) do
      out[#out + 1] = tostring(k) .. "="
      dump(value[k], out)
    end
    out[#out + 1] = "}"
    return out
  end
  local function view()
    return table.concat(dump(tree()), " ")
  end
  local baseline = view()
  assert(not baseline:find("fleet: ", 1, true), "a board with no peers grew a fleet filter")
  for _, answer in ipairs({
    { state = "done", stdout = "" },
    { state = "failed", stdout = "" },
    { state = "pending" },
  }) do
    thurbox.runs["fleetpeers:lead"] = answer
    assert(view() == baseline, "no peers changed the board: " .. answer.state)
  end
  assert(not pane.on_action("fleetqueue.board_scope"), "the fleet key acts with no peer to show")
  local asked = false
  for _, call in ipairs(calls) do
    asked = asked or call.cmd:find("fleet peers --records", 1, true) ~= nil
  end
  assert(asked, "the board never asks for its peers")
elseif scenario == "peers-default" or scenario == "peers-default-narrow" then
  contains("fleet: this")
  contains("Ready")
  assert(not strings(tree()):find("Far away task", 1, true), "the default view drew another fleet's tasks")
  assert(not strings(tree()):find("Elsewhere ready", 1, true), "the default view drew another fleet's tasks")
  contains("downbox/fleet: unreachable 3m")
  contains("local/acme: stale 10m")
  assert(not strings(tree()):find("local/fleet", 1, true), "one fleet visible, and its cards name it")
elseif scenario == "peers-scope" or scenario == "peers-scope-narrow" then
  action("scope")
  contains("fleet: all")
  contains("Far away task")
  -- More than one fleet is visible, so every card says whose it is.
  local labels = {}
  for _, node in ipairs(nodes(tree())) do
    local id = node.id or ""
    if id:find("^board:") then
      labels[id] = (labels[id] or "") .. strings(node)
    end
  end
  assert((labels["board:alpha/03-work"] or ""):find("local/fleet", 1, true), "a local card hides its fleet")
  assert((labels["board:devbox:/srv/fleet#far/01-far"] or ""):find("devbox/fleet", 1, true), "a peer card hides its fleet")
  if scenario == "peers-scope" then
    contains("Ready")
    contains("Elsewhere ready")
    assert(labels["board:local:/other#alpha/01-ready"] and labels["board:alpha/01-ready"], "two fleets' alpha/01-ready became one card")
  end
  assert(#nodes(tree()) < (scenario == "peers-scope" and 300 or 250))
  local seen = { "all" }
  for _ = 1, 4 do
    action("scope")
    seen[#seen + 1] = strings(tree()):match(" fleet: ([^ ]+)")
  end
  assert(
    table.concat(seen, ",") == "all,devbox/fleet,downbox/fleet,local/acme,host",
    "the fleet filter cycles " .. table.concat(seen, ",")
  )
  contains("Working")
  assert(not strings(tree()):find("Far away task", 1, true), "the local host showed a remote fleet")
  action("scope")
  contains("fleet: this")
  action("scope")
  action("scope")
  contains("Far away task")
  assert(not strings(tree()):find("Ready", 1, true), "one peer's view drew this fleet")
  for _, node in ipairs(nodes(tree())) do
    if node.id == "board:devbox:/srv/fleet#far/01-far" then
      assert(not strings(node):find("devbox/fleet", 1, true), "one fleet visible, and its cards name it")
    end
  end
elseif scenario == "peers-readonly" then
  action("scope")
  assert(pane.on_click({ id = "board:devbox:/srv/fleet#far/01-far" }), "a peer card cannot be selected")
  assert(state.board_ref == "devbox:/srv/fleet#far/01-far")
  contains("read-only")
  local before = #commands
  action("enter")
  action("detail")
  assert(#commands == before, "a peer card focused a session here")
  assert(not state.board_detail, "a peer card opened this fleet's record of it")
  assert(state.board_open, "Enter on a peer card closed the board")
  for _, call in ipairs(calls) do
    assert(not call.cmd:find("queue show", 1, true), "a peer card asked this fleet's queue")
  end
  local chips = {}
  for _, run in ipairs(tree().children[#tree().children - 1].children[3].text[1]) do
    chips[#chips + 1] = run.role or ""
  end
  assert(not table.concat(chips, " "):find("board_detail", 1, true), "a peer card offers the record chip")
  assert(pane.on_click({ id = "board:local:/other#alpha/01-ready" }))
  contains("Elsewhere ready")
  assert(pane.on_click({ id = "board:alpha/01-ready" }))
  action("detail")
  contains("Detail")
elseif scenario == "peers-picker" then
  action("scope")
  action("topic")
  local rows = {}
  for _, node in ipairs(nodes(tree())) do
    if (node.id or ""):find("^board%-topic:") then
      rows[#rows + 1] = node.id
    end
  end
  assert(table.concat(rows, ",") == "board-topic:*,board-topic:alpha,board-topic:far", table.concat(rows, ","))
  assert(pane.on_key({ key = "h", char = "h" }), "the fleet key stopped typing into search")
  assert(state.board_picker.field.value == "h")
elseif scenario == "failure" then
  thurbox.runs["fleetqueue:lead"] = { state = "failed", stdout = "" }
  contains("queue unavailable")
  contains("Ready")
  assert(tree().float)
  action("enter")
  contains("Detail")
  action("close")
  assert(pane.on_click({ id = "board:alpha/03-work" }), "visible cached cards lost navigation")
  thurbox.runs["fleetqueue:lead"] = { state = "done", stdout = table.concat(records, "\n") .. "\n" }
  contains("Ready")
  assert(state.board_ref == "alpha/03-work", "recovered queue lost selection")
end
for _, call in ipairs(calls) do
  assert(not call.cmd:find("dispatch", 1, true) and not call.cmd:find("merge", 1, true), "board writes queue")
end
print("board " .. scenario .. " passed")
