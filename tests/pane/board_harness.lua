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
        { text = "░", style = { fg = "muted" } },
        { text = o.label or "" },
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
  return { type = "text", len = 1, text = { { { text = "close" } } } }
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
if scenario == "large" then
  for i = 1, 5000 do
    task("many-" .. i, "queued", "Large " .. i)
  end
end
thurbox.runs["fleetqueue:lead"] = { state = "done", stdout = table.concat(records, "\n") .. "\n" }
thurbox.runs["fleetfuel:lead"] = {
  state = "done",
  stdout = "provider\tfixture\nremaining\t62\nreserve\t20\nwindow\tsession\t62\t2000003000\tsession\n\n",
}
local calls, commands = {}, {}
_G.run = function(key, cmd, opts)
  calls[#calls + 1] = { key = key, cmd = cmd, opts = opts }
end
_G.command = function(name, args)
  commands[#commands + 1] = { name = name, args = args }
end
local pane = dofile("interface/fleet_queue.lua")
assert(pane.on_action("fleetqueue.board"), "the live pane has no board toggle")
local ctx = { width = scenario == "narrow" and 120 or 200, height = scenario == "narrow" and 40 or 50, elapsed = 0 }
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
tree()
if scenario == "columns" then
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
elseif scenario == "landed" then
  assert(not strings(tree()):find("Recent", 1, true))
  action("landed")
  contains("Recent")
elseif scenario == "mouse" then
  pane.on_click({ id = "board:alpha/04-pr" })
  contains("Failing PR")
  assert(pane.on_scroll({ x = 5, y = 10, up = false }))
  contains("Waiting")
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
  assert(state.board_detail_offset == 3)
elseif scenario == "selection" then
  action("enter")
  assert(pane.on_key({ key = "ctrl+c" }) == false, "copy key swallowed")
  for _, n in ipairs(nodes(tree())) do
    assert(n.role ~= "drag", "text selection stolen by pane drag")
  end
elseif scenario == "glyphs" then
  local ready = false
  for _, node in ipairs(nodes(tree())) do
    if node.id == "board:alpha/01-ready" and strings(node):find("Ready", 1, true) then
      ready = strings(node):find("○", 1, true) ~= nil
    end
  end
  assert(ready, "a ready queued card wears the blocked glyph")
elseif scenario == "fuel" then
  contains("62%")
elseif scenario == "narrow" then
  assert(tree().float)
  assert(#nodes(tree()) < 250)
elseif scenario == "large" then
  assert(#nodes(tree()) < 300, "built nodes for the entire queue")
elseif scenario == "fuel-unavailable" then
  thurbox.runs["fleetfuel:lead"] = { state = "done", stdout = "provider\tfixture\nunavailable\tno credentials\n\n" }
  contains("unavailable")
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
  for _, a in ipairs({ "topic", "agent", "needs", "landed", "detail" }) do
    assert(found["action:fleetqueue.board_" .. a], "button lacks native hover/click role: " .. a)
  end
  action("topic")
  contains("topic: alpha")
  action("topic")
  contains("topic: all")
elseif scenario == "missing-session" then
  pane.on_click({ id = "board:alpha/03-work" })
  thurbox.sessions[2] = nil
  action("enter")
  contains("Detail")
elseif scenario == "reserved" then
  assert(not pane.on_key({ key = "ctrl+h" }) and not pane.on_key({ key = "ctrl+l" }))
elseif scenario == "failure" then
  thurbox.runs["fleetqueue:lead"] = { state = "failed", stdout = "" }
  contains("probe did not run")
  assert(tree().float)
  action("enter")
  assert(not state.board_detail, "failed probe retained an invisible selected card")
end
for _, call in ipairs(calls) do
  assert(not call.cmd:find("dispatch", 1, true) and not call.cmd:find("merge", 1, true), "board writes queue")
end
print("board " .. scenario .. " passed")
