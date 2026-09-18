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
--
-- `--accent` prefixes every row with `A` when a span is drawn in the theme's
-- `accent` role and `.` when none is, which is how a mark carried by colour
-- alone — the binding fuel window — is asserted in a text render.
--
-- `--frame` prints the frame instead of the rows: its title, then its
-- top-right overlay, which is where the hide button lives, then its
-- bottom-right overlay, which is where the scroll position lives.
--
-- `--click <text>` clicks the run where `<text>` is drawn — on the frame's
-- overlay or on a row — the way the kernel would: that run's identity becomes
-- the hit handed to `on_click`. It prints `toggled <slot>` for every panel the
-- click toggled, or `nothing toggled`, and then the render as usual.
--
-- `--pills` prints every action-band pill the pane declares, one per line, as
-- `pill <action> <label> <priority>`.
--
-- `--hover <id>` is the identity the pointer is over, so a hover style can be
-- asserted. `--frame` prints each top-right run's `fg`, `bg` and weight too.
--
-- `--leads <shape>` is which Mission Control sessions thurbox reports:
-- `remote-first` a lead mirrored from another host listed before the local
-- one, `two-local` two UNNAMED leads in two checkouts, `two-named` two fleets
-- that named themselves (`· acme`, `· lab`). `--selected <id[,id...]>`
-- is what the session list published in `store.selected`, applied in order with
-- a render after each, so a pane that stays on the fleet you chose while you
-- work in a worker session can be asserted.
--
-- `--chord <key>` is what the key registry answers for the toggle, so a rebind
-- can be rendered. `--fuel-read <seconds>` is how long ago the fuel reading
-- was taken; the default is two minutes, inside the pane's own TTL.
-- `--fuel-accounts` replaces the one reading with two: the same provider under
-- two logins, which is what a fleet whose `agent.conf` names a second account
-- draws.
-- `--fuel-name-collision` is the fixture that tells `checkout` apart from a
-- coincidence: the checkout's own account name differs from its provider, and
-- the named account's agent is called the same as its own vendor — the one
-- shape where deciding the parentheses by comparing `account` against
-- `provider` disagrees with deciding it by the `checkout` flag.
--
-- Scrolling, applied in this order before anything is printed:
--   `--long <n>`    adds a topic of n running tasks, a queue longer than a pane
--   `--height <h>`  the pane's outer height (default 200: everything fits)
--   `--wheel <n>`   n wheel ticks, down when positive and up when negative,
--                   with a render after each tick as the kernel does; may repeat
--   `--action <a>`  runs a declared action, as its chord or the palette would

local WIDTH = tonumber(arg[1] or "") or 44
local MARKS, ACCENT, FRAME, PILLS = false, false, false, false
local CHORD, CLICK, HOVER, FUEL_READ = "f3", nil, nil, 120
local LONG, HEIGHT, WHEEL, ACTION = 0, 200, {}, nil
local SELECTED = {}
-- `--long-label` adds a third fuel window whose label is as long as the pane
-- lets a label be, the shape a per-model window takes.
local LONG_LABEL = false
-- `--fuel-accounts` is a fleet spending TWO LOGINS of one provider, which is
-- the reading `fleet status` takes when `agent.conf` carries an `ENV` line:
-- two records naming the same provider, told apart by `account` alone.
local FUEL_ACCOUNTS = false
local FUEL_NAME_COLLISION = false
for i, a in ipairs(arg) do
  if a == "--long-label" then
    LONG_LABEL = true
  elseif a == "--fuel-accounts" then
    FUEL_ACCOUNTS = true
  elseif a == "--fuel-name-collision" then
    FUEL_NAME_COLLISION = true
  elseif a == "--long" then
    LONG = tonumber(arg[i + 1]) or 0
  elseif a == "--height" then
    HEIGHT = tonumber(arg[i + 1]) or HEIGHT
  elseif a == "--wheel" then
    WHEEL[#WHEEL + 1] = tonumber(arg[i + 1]) or 0
  elseif a == "--action" then
    ACTION = arg[i + 1]
  end
  if a == "--marks" then
    MARKS = true
  elseif a == "--accent" then
    ACCENT = true
  elseif a == "--frame" then
    FRAME = true
  elseif a == "--pills" then
    PILLS = true
  elseif a == "--chord" then
    CHORD = arg[i + 1]
  elseif a == "--click" then
    CLICK = arg[i + 1]
  elseif a == "--hover" then
    HOVER = arg[i + 1]
  elseif a == "--fuel-read" then
    FUEL_READ = tonumber(arg[i + 1]) or FUEL_READ
  elseif a == "--selected" then
    for id in (arg[i + 1] or ""):gmatch("[^,]+") do
      SELECTED[#SELECTED + 1] = id
    end
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
  role = function(name)
    return name
  end,
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
    return CHORD
  end,
}

local toggled = {}

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
    id = function(id)
      return id ~= nil and id == HOVER
    end,
    role = function(role)
      return role ~= nil and role == HOVER
    end,
  }
end
package.preload["lib.panels"] = function()
  return { toggle = function(name)
    toggled[#toggled + 1] = name
  end, shown = function()
    return true
  end }
end

-- The kernel's globals.
local NOW = 1757400000
_G.state = { offset = 0 }
-- The bus the session list publishes its selection on, which is how this pane
-- knows which fleet you are looking at. Empty until `--selected` says otherwise.
_G.store = {}
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
        publish = { "attested", "merged", ago(20) },
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
        publish = { "attested", "green", ago(9) },
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
        publish = { "attested", "open", ago(3) },
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
        publish = { "attested", "merged", ago(2870) },
      },
    },
  },
}

-- A queue longer than the pane, for the scroll window: one topic of running
-- work, so every row it adds is a row the operator would want to reach.
if LONG > 0 then
  local tasks = {}
  for n = 1, LONG do
    tasks[n] = {
      id = ("%02d-long-task"):format(n), state = "dispatched",
      title = ("Long task number %d"):format(n), brief = 1, moved = ago(n),
    }
  end
  table.insert(TOPICS, 1, { slug = "long-queue", title = "A queue longer than the pane", tasks = tasks })
end

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

-- Two windows on two clocks, and the LONGER one binds. That is the reading
-- the block used to flip on: it drew only the binding window, so the row
-- changed meaning whenever the two percentages crossed.
--
-- The binding window is also UNDER the reserve and the other is not, so the
-- render has one row that must say `low` and one that must not.
local FUEL = table.concat({
  "provider\tclaude",
  "remaining\t18",
  "reserve\t20",
  "limited_by\tseven_day",
  "read_at\t" .. (NOW - FUEL_READ),
  "window\tfive_hour\t62\t" .. (NOW + 3 * 3600 + 600) .. "\tsession",
  "window\tseven_day\t18\t" .. (NOW + 4 * 86400 + 7200) .. "\tweek",
}, "\n")
if LONG_LABEL then
  FUEL = FUEL .. "\nwindow\tmodel_week\t40\t" .. (NOW + 2 * 86400) .. "\tModel week"
end

-- The same provider read under two accounts: the first is the checkout's own
-- and the second the login an agent's `ENV` selects. The numbers are far apart
-- on purpose — a pane that drew one reading twice would still look right.
if FUEL_ACCOUNTS then
  FUEL = table.concat({
    "provider\tclaude",
    "account\tclaude",
    "checkout\t1",
    "remaining\t18",
    "reserve\t20",
    "limited_by\tseven_day",
    "read_at\t" .. (NOW - FUEL_READ),
    "window\tfive_hour\t62\t" .. (NOW + 3 * 3600 + 600) .. "\tsession",
    "window\tseven_day\t18\t" .. (NOW + 4 * 86400 + 7200) .. "\tweek",
    "",
    "provider\tclaude",
    "account\tclaude-spare",
    "checkout\t0",
    "remaining\t70",
    "reserve\t20",
    "limited_by\tfive_hour",
    "read_at\t" .. (NOW - FUEL_READ),
    "window\tfive_hour\t70\t" .. (NOW + 2 * 3600) .. "\tsession",
    "window\tseven_day\t95\t" .. (NOW + 5 * 86400) .. "\tweek",
  }, "\n")
end

-- The checkout's own account named `lead`, reading a provider called
-- `codex` — its own name and its provider disagree, the way an operator's
-- `AGENT=` line and `FUEL_PROVIDER` commonly do. Beside it, a NAMED account
-- (`checkout\t0`) whose agent is called the same as its own vendor. Deciding
-- the parentheses by comparing `account` against `provider` gets BOTH of
-- these backwards; deciding it by the `checkout` flag gets both right.
if FUEL_NAME_COLLISION then
  FUEL = table.concat({
    "provider\tcodex",
    "account\tlead",
    "checkout\t1",
    "remaining\t40",
    "reserve\t20",
    "limited_by\tseven_day",
    "read_at\t" .. (NOW - FUEL_READ),
    "window\tseven_day\t40\t" .. (NOW + 4 * 86400) .. "\tweek",
    "",
    "provider\tcodex",
    "account\tcodex",
    "checkout\t0",
    "remaining\t85",
    "reserve\t20",
    "limited_by\tseven_day",
    "read_at\t" .. (NOW - FUEL_READ),
    "window\tseven_day\t85\t" .. (NOW + 4 * 86400) .. "\tweek",
  }, "\n")
end

local LEAD = { id = "s1", name = "⌖ Mission Control", cwd = "/home/operator/fleet", status = "ok" }
_G.thurbox.sessions = { LEAD }
_G.thurbox.runs = {
  ["fleetqueue:s1"] = { state = "ok", stdout = table.concat(out, "\n") .. "\n" },
  ["fleetfuel:s1"] = { state = "ok", stdout = FUEL .. "\n" },
}

-- `--leads remote-first`: a lead MIRRORED from another machine, listed before
-- the local one, whose queue is empty — thurbox sets `host` on a session it
-- reaches over ssh or wsl and leaves it nil on this machine's. One remote lead
-- beside one local one is an ordinary setup, so the pane has to draw the local
-- queue and say nothing about the other.
--
-- `--leads two-local`: two leads on THIS machine in different checkouts, which
-- is a choice the pane cannot make for the operator.
local LEADS
for i, a in ipairs(arg) do
  if a == "--leads" then
    LEADS = arg[i + 1]
  end
end
if LEADS == "remote-first" then
  local remote = { id = "r1", name = "⌖ Mission Control", cwd = "/home/lab/fleet", status = "ok", host = "labhost" }
  _G.thurbox.sessions = { remote, LEAD }
  _G.thurbox.runs["fleetqueue:r1"] = { state = "ok", stdout = "R\t/home/lab/fleet/orchestration/queue\n" }
  _G.thurbox.runs["fleetfuel:r1"] = { state = "ok", stdout = FUEL .. "\n" }
elseif LEADS == "two-local" then
  local other = { id = "s2", name = "⌖ Mission Control", cwd = "/home/operator/fleet-copy", status = "ok" }
  _G.thurbox.sessions = { LEAD, other }
elseif LEADS == "two-named" then
  -- TWO FLEETS, each its own checkout and its own name — what
  -- `orchestration/fleet.conf` renders into the manifest. `s1` keeps the whole
  -- fixture queue; `s2` has a small one of its own, so which queue is drawn is
  -- a question the render answers by itself. `w9` is an ordinary worker, and
  -- selecting it is how the sticky binding is asserted.
  local acme = { id = "s1", name = "⌖ Mission Control · acme", cwd = "/home/operator/fleet", status = "ok" }
  local lab = { id = "s2", name = "⌖ Mission Control · lab", cwd = "/home/operator/fleet-lab", status = "ok" }
  local worker = { id = "w9", name = "🚀 Calibrate the lab rig", cwd = "/home/operator/work/rig", status = "ok" }
  _G.thurbox.sessions = { acme, lab, worker }
  _G.thurbox.runs["fleetqueue:s2"] = { state = "ok", stdout = table.concat({
    "R\t/home/operator/fleet-lab/orchestration/queue",
    "T\tlab-rig\tThe lab's own topic",
    table.concat({ "K", "01-calibrate", "dispatched", "Calibrate the lab rig",
      "", "", "", "1", "0", "0", "feat/rig", tostring(NOW - 900), "", "", "0" }, "\t"),
  }, "\n") .. "\n" }
  _G.thurbox.runs["fleetfuel:s2"] = { state = "ok", stdout = FUEL .. "\n" }
end

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
    local text, bold, accent = "", false, false
    for _, span in ipairs(spans) do
      text = text .. (span.text or "")
      bold = bold or (span.style and span.style.bold) or false
      accent = accent or (span.style and span.style.fg == "accent") or false
    end
    sink[#sink + 1] = { text = text, bold = bold, accent = accent }
  end
  return sink
end

-- `render` is handed the pane's OUTER width; the pane spends two columns on
-- its border, so this asks for the border too and reports the inner rows.
local ctx = { width = WIDTH + 2, height = HEIGHT, elapsed = 0 }
if SELECTED[1] then
  _G.store.selected = SELECTED[1]
end
local tree = pane.render(ctx)
-- Each later selection is a separate frame, the way the kernel re-renders when
-- the session list publishes a new one.
for index = 2, #SELECTED do
  _G.store.selected = SELECTED[index]
  tree = pane.render(ctx)
end
for _, ticks in ipairs(WHEEL) do
  for _ = 1, math.abs(ticks) do
    pane.on_scroll({ up = ticks < 0, x = 1, y = 1 })
    tree = pane.render(ctx)
  end
end
if ACTION then
  pane.on_action(ACTION)
  tree = pane.render(ctx)
end

if PILLS then
  for _, pill in ipairs(pane.pills or {}) do
    print(("pill %s %s %s"):format(pill.action, pill.label, tostring(pill.priority)))
  end
  return
end

--- A frame slot's runs, which may be one span or a list of them.
local function runs_of(text)
  if type(text) ~= "table" then
    return {}
  end
  if type(text.text) == "string" then
    return { text }
  end
  return text
end

--- Every run the tree paints, overlay slots first, then each row's spans.
local function runs_in(node, sink)
  if node.type == "box" then
    for _, child in ipairs(node.children or {}) do
      runs_in(child, sink)
    end
    return sink
  end
  local rows = node.text
  if rows[1] and type(rows[1].text) == "string" then
    rows = { rows }
  end
  for _, spans in ipairs(rows) do
    for _, span in ipairs(spans) do
      sink[#sink + 1] = span
    end
  end
  return sink
end

local function slot_text(runs)
  local text = ""
  for _, run in ipairs(runs) do
    text = text .. run.text
  end
  return text
end

if CLICK then
  -- The run whose text holds what was clicked is the hit — exactly the
  -- identity the kernel records for a run that names one. A run with neither
  -- an `id` nor a `role` records none, so the click lands on the nearest
  -- target under it, which for a row is the pane's own root identity.
  local overlay = (tree.frame or {}).overlay or {}
  local runs = {}
  for _, slot in ipairs({ "top_left", "top_right", "bottom_left", "bottom_right" }) do
    for _, run in ipairs(runs_of(overlay[slot])) do
      runs[#runs + 1] = run
    end
  end
  runs_in(tree, runs)
  for _, run in ipairs(runs) do
    if run.text:find(CLICK, 1, true) then
      local id, role = run.id, run.role
      if not (id or role) then
        id, role = tree.id, tree.role
      end
      if (id or role) and pane.on_click then
        pane.on_click({ id = id, role = role, class = "", x = 0, y = 0,
          w = width_of(run.text), h = 1, dragging = false })
        tree = pane.render(ctx)
      end
      break
    end
  end
  if #toggled == 0 then
    print("nothing toggled")
  end
  for _, name in ipairs(toggled) do
    print("toggled " .. name)
  end
end

if FRAME then
  local frame = tree.frame or {}
  local overlay = frame.overlay or {}
  print("title: " .. slot_text(runs_of(frame.title)))
  print("top_right: " .. slot_text(runs_of(overlay.top_right)))
  local styles = {}
  for _, run in ipairs(runs_of(overlay.top_right)) do
    local style = run.style or {}
    styles[#styles + 1] = ("fg=%s bg=%s%s"):format(
      tostring(style.fg), tostring(style.bg), style.bold and " bold" or "")
  end
  print("top_right_style: " .. table.concat(styles, " | "))
  print("bottom_right: " .. slot_text(runs_of(overlay.bottom_right)))
  print("root: " .. tostring(tree.id or tree.role or ""))
  return
end

for _, row in ipairs(lines_of(tree, {})) do
  local text = (row.text:gsub("%s+$", ""))
  if MARKS then
    print((row.bold and "B " or ". ") .. text)
  elseif ACCENT then
    print((row.accent and "A " or ". ") .. text)
  else
    print(text)
  end
end
