-- Render `interface/fleet_queue.lua` offline, as text, so its LAYOUT can be
-- asserted on.
--
-- WHY THIS EXISTS. Every other subsystem here has a selftest and the pane had
-- none, because the only way to see it was to install it and look. So the one
-- thing nobody could check was the one thing the pane is for: how much it
-- draws, and whether the running work is the part you see first. The report
-- that produced this file — "custom pane displays too many elements, it is not
-- clear what we are really working on" — is a layout claim, and a layout claim
-- needs a rendering to argue with.
--
-- WHAT IT IS NOT. Not thurbox. The pane's four `lib.*` modules are stubbed
-- here, so this proves what the pane BUILDS, never what the terminal paints.
-- The stubs are faithful to the two things every budget in the pane depends
-- on: `widgets.len` counts columns (not bytes) and `Row:trailing` drops a note
-- that does not fit, which is `ui.lua`'s own rule. Anything past that — real
-- colours, real fonts, a multiplexer that disagrees about a glyph's width — is
-- the operator's terminal and is out of scope by construction.
--
-- Usage:
--   lua scripts/lib/pane_harness.lua <width> [--marks]
--
-- `--marks` prefixes every row with `B` when it carries a bold span and `.`
-- when it does not, which is how "the running work is the dominant thing" is
-- asserted rather than admired.

local WIDTH = tonumber(arg[1] or "") or 44
local MARKS = false
for _, a in ipairs(arg) do
  if a == "--marks" then
    MARKS = true
  end
end

-- Columns, not bytes. Every glyph the pane draws is one cell except the fuel
-- mark, which is East_Asian_Width WIDE — the pane's own header calls that out
-- as the reason the glyph is a setting, so the stub has to agree with it.
local WIDE = {
  { 0x1100, 0x115F }, { 0x2E80, 0xA4CF }, { 0xAC00, 0xD7A3 },
  { 0xF900, 0xFAFF }, { 0xFE30, 0xFE6F }, { 0xFF00, 0xFF60 },
  { 0xFFE0, 0xFFE6 }, { 0x1F300, 0x1FAFF }, { 0x26FD, 0x26FD },
}

local function width_of(str)
  local n = 0
  for _, code in utf8.codes(str or "") do
    n = n + 1
    for _, span in ipairs(WIDE) do
      if code >= span[1] and code <= span[2] then
        n = n + 1
        break
      end
    end
  end
  return n
end

--- Keep the first `cols` columns, appending `mark` when anything was cut.
local function cut(str, cols, mark)
  mark = mark or ""
  if width_of(str) <= cols then
    return str
  end
  local budget = cols - width_of(mark)
  local out, used = "", 0
  for _, code in utf8.codes(str) do
    local ch = utf8.char(code)
    local w = width_of(ch)
    if used + w > budget then
      break
    end
    out, used = out .. ch, used + w
  end
  return out .. mark
end

local widgets = {}
widgets.len = width_of
widgets.chars = function(s)
  return utf8.len(s or "") or #(s or "")
end
widgets.truncate = function(s, w)
  return cut(s, math.max(0, w), "…")
end
widgets.truncate_hard = function(s, max)
  if max <= 1 and width_of(s) > max then
    return ""
  end
  return cut(s, math.max(0, max), "…")
end
widgets.keep_left = function(s, max)
  return cut(s, math.max(0, max))
end
widgets.keep_right = function(s, max)
  return cut(s, math.max(0, max))
end
widgets.middle_truncate = function(s, w)
  if w < 8 or width_of(s) <= w then
    return cut(s, w, "…")
  end
  local head = math.floor((w - 1) / 2)
  return cut(s, head) .. "…" .. string.sub(s, -(w - 1 - head))
end
widgets.pad = function(s, w)
  return s .. string.rep(" ", math.max(0, w - width_of(s)))
end
widgets.now_ms = function()
  return _G.thurbox.taken_at_ms
end
widgets.time_ago = function(millis, now)
  local elapsed = math.floor(math.max(0, (now or widgets.now_ms()) - millis) / 1000)
  if elapsed < 60 then
    return elapsed .. "s ago"
  elseif elapsed < 3600 then
    return math.floor(elapsed / 60) .. "m ago"
  elseif elapsed < 86400 then
    return math.floor(elapsed / 3600) .. "h ago"
  end
  return math.floor(elapsed / 86400) .. "d ago"
end

local theme = setmetatable({
  spinner_frame = function()
    return "◐"
  end,
  dim = function(str)
    return { { text = str, style = { fg = "muted" } } }
  end,
}, {
  -- Any role the pane names resolves to its own name, so a colour added to the
  -- pane never has to be added here as well.
  __index = function(_, key)
    return key
  end,
})

widgets.divider = function(w, char)
  return { type = "text", len = 1, text = theme.dim(string.rep(char or "─", math.max(0, w))) }
end

-- `ui.lua`'s Row, including the one rule the pane's budgets lean on: a
-- trailing note is DROPPED rather than overflowed when fewer than four columns
-- are left for it.
local Row = {}
Row.__index = Row
local SEPARATOR, MIN_TRAILING = "  ", 4

function Row:_tone(style)
  if style == nil or self.tone == nil then
    return style
  end
  return self.tone(style)
end

function Row:add(run, style)
  if run == nil or run == "" then
    return self
  end
  self.spans[#self.spans + 1] = { text = run, style = self:_tone(style) }
  self.used = self.used + width_of(run)
  return self
end

function Row:gap(n)
  return self:add(string.rep(" ", math.max(0, n or 1)))
end

function Row:trailing(note, style)
  if not note or note == "" then
    return self
  end
  if not self.width then
    return self:add(SEPARATOR):add(note, style)
  end
  local avail = math.max(0, self.width - self.used - width_of(SEPARATOR))
  if avail < MIN_TRAILING then
    return self
  end
  self:add(SEPARATOR)
  return self:add(widgets.truncate_hard(note, avail), style)
end

function Row:spans_list()
  return self.spans
end

local ui = {
  row = function(opts)
    opts = opts or {}
    return setmetatable({ spans = {}, used = 0, width = opts.width, tone = opts.tone }, Row)
  end,
  chord = function()
    return "f3"
  end,
}

package.preload["lib.widgets"] = function()
  return widgets
end
package.preload["lib.theme"] = function()
  return theme
end
package.preload["lib.ui"] = function()
  return ui
end
package.preload["lib.panels"] = function()
  return { toggle = function() end, shown = function()
    return true
  end }
end

-- The kernel's globals.
local NOW = 1757400000
_G.state = { offset = 0 }
_G.run = function() end
_G.thurbox = { taken_at_ms = NOW * 1000, sessions = {}, runs = {} }

-- The queue this renders. It is the shape of the screen the report was about:
-- a RUNNING topic that also holds a task the forge already merged, a chain of
-- blockers of which one is long since cleared, tasks nothing has emitted an
-- event for, and one settled topic underneath. It also holds a task waiting on
-- a CONDITION outside the queue, which is the blocker nothing will ever clear
-- on its own.
--
-- It also holds the publish state EVERY task passes through and no fixture used
-- to reach: `open`, on a task `shepherd` linked by head branch before `collect`
-- read the worker's result. That is why the pane's most common publish row went
-- unrendered by anything that could be argued with, and it is the row the
-- operator's next move — review it — hangs off.
local function ago(minutes)
  return NOW - minutes * 60
end

local TOPICS = {
  {
    slug = "publish-agnostic",
    title = "Make the publish method a task's own declaration",
    tasks = {
      {
        id = "01-declare-publish-method", state = "landed", title = "Declare the publish method on the task",
        outcome = "shipped", artifact = "https://github.com/Thurbeen/fleet/pull/43",
        brief = 1, events = 14, result = 1, moved = ago(24),
        publish = { "no-mistakes", "merged", ago(20) },
      },
      {
        id = "02-shepherd-records-publish", state = "dispatched",
        title = "Record the publish state the shepherd already sees",
        blockers = "publish-agnostic/01-declare-publish-method|semantic-dependency",
        brief = 1, events = 0, moved = ago(15),
      },
      {
        id = "03-draw-publish-row", state = "queued", title = "Draw the publish row in the TUI queue pane",
        blockers = "publish-agnostic/02-shepherd-records-publish|semantic-dependency",
        brief = 1, events = 0, moved = ago(15),
      },
    },
  },
  {
    slug = "pane-declutter",
    title = "Make the queue pane answer what the fleet is working on",
    tasks = {
      {
        id = "01-declutter-the-pane", state = "dispatched",
        title = "Cut the pane back to what the operator acts on",
        blockers = "publish-agnostic/03-draw-publish-row|semantic-dependency",
        brief = 1, events = 3, moved = ago(6),
      },
    },
  },
  {
    slug = "retire-webui",
    title = "Retire the web monitor",
    tasks = {
      {
        id = "01-remove-the-monitor", state = "done", title = "Remove the web monitor and everything that starts it",
        outcome = "shipped", artifact = "https://github.com/Thurbeen/fleet/pull/47",
        brief = 1, events = 9, result = 1, moved = ago(48),
        publish = { "no-mistakes", "green", ago(9) },
      },
      {
        -- Held by a CONDITION rather than by a task: the second form of
        -- blocker, which no event releases. It is here because the pane is
        -- where the operator sees that the wait has no actor but them.
        --
        -- Quoted, because the probe hands the condition over as the raw YAML
        -- scalar and free prose holding `: ` is written quoted on disk. The
        -- quotes are the writer's, not the operator's, and the pane draws
        -- neither of them.
        id = "02-point-the-docs-at-the-pane", state = "queued", title = "Point every document at the pane",
        blockers = "!'az login: for the tenant'|missing-credential",
        brief = 0, events = 0, moved = ago(48),
      },
      {
        id = "03-drop-the-webui-selftest", state = "dispatched", title = "Drop the monitor's selftest and its CI job",
        -- A GitLab merge request, so the pane is rendered against both
        -- forges fleet ships an adapter for rather than only one.
        artifact = "https://gitlab.example.com/acme/group/widgets/-/merge_requests/52",
        brief = 1, events = 2, result = 1, moved = ago(3),
        publish = { "no-mistakes", "open", ago(3) },
      },
    },
  },
  {
    slug = "remote-dispatch",
    title = "Dispatch a task to a remote host",
    tasks = {
      {
        id = "01-probe-the-host", state = "landed", title = "Probe the host before spawning anything",
        outcome = "shipped", artifact = "https://github.com/Thurbeen/fleet/pull/31",
        brief = 1, events = 11, result = 1, moved = ago(2880),
        publish = { "no-mistakes", "merged", ago(2870) },
      },
    },
  },
}

local out = { "R\t/home/operator/fleet/orchestration/queue" }
for _, topic in ipairs(TOPICS) do
  out[#out + 1] = table.concat({ "T", topic.slug, topic.title }, "\t")
  for _, t in ipairs(topic.tasks) do
    local pub = t.publish or { "", "", 0 }
    out[#out + 1] = table.concat({
      "K", t.id, t.state, t.title, t.outcome or "", t.artifact or "", t.blockers or "",
      tostring(t.brief or 0), tostring(t.events or 0), tostring(t.result or 0),
      "feat/" .. t.id, tostring(t.moved or 0), pub[1], pub[2], tostring(pub[3]),
    }, "\t")
  end
end
out[#out + 1] = "A\t7"

local FUEL = table.concat({
  "provider\tclaude",
  "remaining\t62",
  "reserve\t15",
  "read_at\t" .. (NOW - 120),
}, "\n")

local LEAD = { id = "s1", name = "⌖ Mission Control", cwd = "/home/operator/fleet", status = "ok" }
_G.thurbox.sessions = { LEAD }
_G.thurbox.runs = {
  ["fleetqueue:s1"] = { state = "ok", stdout = table.concat(out, "\n") .. "\n" },
  ["fleetfuel:s1"] = { state = "ok", stdout = FUEL .. "\n" },
}

local here = (arg[0]:match("^(.*)/scripts/lib/") or ".")
local pane = assert(loadfile(here .. "/interface/fleet_queue.lua"))()

--- Every line a node carries, as `{ text, bold }`.
local function lines_of(node, sink)
  if node.type == "box" then
    for _, child in ipairs(node.children or {}) do
      lines_of(child, sink)
    end
    return sink
  end
  -- `text` is either a list of span lists (one per line) or a single span list.
  local rows = node.text
  if rows[1] and rows[1].text ~= nil and type(rows[1].text) == "string" then
    rows = { rows }
  end
  for _, spans in ipairs(rows) do
    local text, bold = "", false
    for _, span in ipairs(spans) do
      text = text .. (span.text or "")
      bold = bold or (span.style and span.style.bold) or false
    end
    sink[#sink + 1] = { text = text, bold = bold }
  end
  return sink
end

-- `render` is handed the pane's OUTER width; the pane spends two columns on
-- its border, so this asks for the border too and reports the inner rows.
local tree = pane.render({ width = WIDTH + 2, height = 200, elapsed = 0 })
for _, row in ipairs(lines_of(tree, {})) do
  local text = (row.text:gsub("%s+$", ""))
  if MARKS then
    print((row.bold and "B " or ". ") .. text)
  else
    print(text)
  end
end
