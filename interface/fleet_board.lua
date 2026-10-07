-- Full-screen, read-only projection of fleet_queue's already parsed records.
-- This library shares its caller's trust, queue/fuel probes and fleet choice.
-- Node roles leave links to thurbox’s native handler.
local ui = require("lib.ui")
local widgets = require("lib.widgets")
local theme = require("lib.theme")
local hover = require("lib.hover")
local scroll = require("lib.scroll")
local M = {}
local headings =
  { "Queued / waiting", "Dispatched / working", "Shipped", "Served", "Stuck / abandoned", "Landed · recent" }
local statuses = { "blocked", "working", "done", "blocked", "error", "done" }
local columns, topics, agents, geometry, lead, selected = { {}, {}, {}, {}, {}, {} }, {}, {}, {}, nil, nil
local cached_model, cached_filter, locations, heights_by_column = nil, nil, {}, {}
local function span(value, color, role)
  return { text = value, style = { fg = color or theme.text }, role = role }
end
local function text(spans, height)
  return { type = "text", len = height or 1, text = { spans } }
end
local function chip(label, action, on)
  local role = "action:fleetqueue.board_" .. action
  return {
    text = " " .. label .. " ",
    role = role,
    style = hover.style(role, hover.button_style(), {
      fg = on and theme.role("inverted_fg") or theme.secondary,
      bg = on and theme.accent or theme.role("selection_bg"),
      bold = on,
    }),
  }
end
local function url(value)
  return type(value) == "string" and value:match("^https?://[^%s]+$") and value or nil
end
local function linked(value)
  local out, pos = {}, 1
  for first, link, last in value:gmatch("()(https?://[^%s]+)()") do
    out[#out + 1] = span(value:sub(pos, first - 1), theme.secondary)
    out[#out + 1] = span(link, theme.accent, "url:" .. link)
    pos = last
  end
  out[#out + 1] = span(value:sub(pos), theme.secondary)
  return out
end
local function needs(task)
  if
    task.state == "stuck"
    or task.state == "abandoned"
    or task.state == "failed"
    or task.publish_method == "served" and task.state == "done"
    or task.publish_state == "checks-failed"
    or task.publish_state == "changes-requested"
    or task.publish_state == "awaiting-approval"
    or task.publish_state == "threads-open"
    or (task.threads or 0) > 0
  then
    return true
  end
  for _, edge in ipairs(task.blocked_by) do
    if not edge.cleared and (edge.condition or edge.kind:find("approval", 1, true)) then
      return true
    end
  end
  return false
end
local function category(task, now)
  if task.state == "landed" then
    if task.moved_at > 0 and now - task.moved_at <= 86400 then
      return 6
    end
  elseif task.state == "stuck" or task.state == "abandoned" or task.state == "failed" then
    return 5
  elseif task.state == "done" then
    return task.publish_method == "served" and 4 or 3
  elseif task.state == "dispatched" then
    return 2
  else
    return 1
  end
end
local function choose(c, n)
  c = math.max(1, math.min(6, c))
  state.board_column = c
  state.board_index = math.max(1, math.min(#columns[c], n or 1))
  selected = columns[c][state.board_index]
  state.board_ref = selected and selected.ref
end
local function session(task)
  for _, s in ipairs(thurbox.sessions or {}) do
    if task.session ~= "" and s.id == task.session then
      return s
    end
  end
end
local function note(task)
  local out = {}
  for _, edge in ipairs(task.blocked_by) do
    if not edge.cleared then
      out[#out + 1] = (edge.condition and "condition: " or (edge.kind .. ": ")) .. edge.ref
    end
  end
  if #out == 0 then
    out[1] = task.publish_detail ~= "" and task.publish_detail
      or task.publish_state ~= "" and task.publish_state
      or task.display_state
  end
  if task.state == "done" and task.publish_method ~= "served" then
    out[#out + 1] = task.publish_state == "green" and "yours to merge" or "review: unknown · auto-merge: unknown"
    out[#out + 1] = task.threads and task.threads .. " unresolved threads" or "threads: unknown"
  elseif (task.threads or 0) > 0 then
    out[#out + 1] = task.threads .. " unresolved threads"
  end
  return table.concat(out, " · ")
end
local function card_spans(row, width)
  local task = row.task
  local b = ui.row({ width = width })
  local tone = theme.role(({ "accent", "status_done", "branch_name" })[task.topic_index % 3 + 1])
  if row.line == 1 then
    b:add(" " .. widgets.truncate(task.ref, math.max(1, width - 12)), { fg = tone, bold = true })
    local seconds = math.max(0, (geometry.now or 0) - task.moved_at)
    local age = task.moved_at == 0 and "?"
      or seconds < 3600 and math.floor(seconds / 60) .. "m"
      or seconds < 86400 and math.floor(seconds / 3600) .. "h"
      or math.floor(seconds / 86400) .. "d"
    b:trailing(task.display_state .. " " .. age, { fg = theme.muted })
  elseif row.line == 2 then
    local status = task.display_state == "queued" and "idle"
      or task.display_state == "waiting" and "blocked"
      or task.state == "dispatched" and "working"
      or statuses[row.column]
    local spec = ui.status(status, row.elapsed)
    b:add(" " .. spec.glyph .. " ", { fg = spec.color or theme.accent })
    b:add(widgets.truncate(task.title, math.max(0, width - 4)), { fg = theme.text, bold = true })
  elseif row.line == 3 then
    local live = session(task)
    local host = task.host ~= ""
        and ((thurbox.theme.nerd_font and "" or "▣") .. " " .. widgets.truncate(task.host, 8))
      or "⌂"
    b:add(
      " "
        .. host
        .. " "
        .. widgets.truncate(task.agent ~= "" and task.agent or "unknown", math.max(6, math.floor(width / 3)))
        .. " ",
      { fg = theme.muted }
    )
    local spec = live and theme.status(live.status or "unreported") or { glyph = "—", color = theme.muted }
    b:add(spec.glyph .. " " .. (live and (live.status or "unreported") or "no session"), { fg = spec.color })
  elseif row.line == 4 then
    local link = url(task.artifact)
    if link then
      local number = link:match("/pull/(%d+)") or link:match("/merge_requests/(%d+)")
      local label = number and (link:find("/pull/", 1, true) and "PR #" or "MR !") .. number or "artifact"
      local spans = {
        span(" " .. label, theme.accent, "url:" .. link),
        span(
          " " .. widgets.truncate(note(task), math.max(0, width - #label - 11)),
          task.publish_state == "checks-failed" and theme.bad or theme.muted
        ),
      }
      if url(task.review) then
        spans[#spans + 1] = span(" review", theme.accent, "url:" .. task.review)
      end
      return spans
    end
    b:add(
      " " .. widgets.truncate(note(task), math.max(0, width - 2)),
      { fg = needs(task) and theme.warn or theme.muted }
    )
  end
  return b:spans_list()
end
local function column_node(c, width, height, elapsed)
  local cards = columns[c]
  local inner = math.max(1, height - 2)
  local heights = heights_by_column[c] or {}
  local index = state.board_column == c and state.board_index or 1
  local first, visible = 1, 0
  if #cards > 0 and (c ~= 6 or state.board_landed) then
    first, visible = scroll.window_variable(heights, 0, index, inner)
  end
  local rows = {}
  for n = first, math.min(#cards, first + visible - 1) do
    for line = 1, 5 do
      rows[#rows + 1] = { task = cards[n], column = c, line = line, elapsed = elapsed }
    end
  end
  local body = ui.list({
    items = rows,
    width = width - 2,
    height = inner,
    pad = true,
    cursor = state.board_column == c and ((index - first) * 5 + 1) or 0,
    id_of = function(row)
      return "board:" .. row.task.ref
    end,
    row = function(row)
      return card_spans(row, width - 2)
    end,
  })
  local selection
  for _, node in ipairs(body.children or {}) do
    if node.class and node.class:find("selected") then
      selection = node.style
    end
  end
  for _, node in ipairs(body.children or {}) do
    if selected and node.id == "board:" .. selected.ref then
      node.style = selection
    end
  end
  local bar = {}
  if visible > 0 and visible < #cards then
    local thumb = math.max(1, math.floor(inner * visible / #cards))
    local top = math.floor((inner - thumb) * (first - 1) / math.max(1, #cards - visible))
    for n = 0, inner - 1 do
      bar[#bar + 1] = span(
        n >= top and n < top + thumb and "┃" or "│",
        n >= top and n < top + thumb and theme.accent or theme.border
      )
    end
  end
  if c == 6 and not state.board_landed then
    body = {
      text({
        state.board_picker and span(" Recent landed folded", theme.muted)
          or chip("Show recent landed", "landed", false),
      }),
    }
  end
  local panel = ui.panel({
    title = theme.status(statuses[c]).glyph .. " " .. headings[c] .. " · " .. #cards,
    focused = state.board_column == c and not state.board_picker,
    body = body,
    right_column = bar,
  })
  panel.fill = 1
  panel.id = "board-column:" .. c
  return panel
end
local function fuel_node(fuel, width)
  local cell_width = math.floor((width - 1) / 2)
  local label_width = math.min(24, math.floor(cell_width / 2))
  local rows = {}
  for _, rec in ipairs(fuel or {}) do
    local windows = rec.windows or {}
    if #windows == 0 and rec.remaining then
      windows = { { label = rec.limited_by or "remaining", remaining = rec.remaining } }
    end
    if rec.unavailable or #windows == 0 then
      rows[#rows + 1] = text({ span(" " .. (rec.provider or "Fuel") .. " unavailable", theme.warn) })
    else
      for _, window in ipairs(windows) do
        local pct = math.max(0, math.min(100, window.remaining))
        local name = (rec.provider or "Fuel") .. " " .. window.label .. (rec.stale and " · stale" or "")
        local label = pct .. "%"
        local gauge = widgets.gauge(pct / 100, {
          width = math.max(6, cell_width - label_width - widgets.len(label) - 1),
          style = { fg = pct <= (rec.reserve or 0) and theme.bad or theme.ok },
          label = label,
        })
        if gauge.text and gauge.text[1] and gauge.text[1][2] then
          gauge.text[1][2].style = { fg = theme.muted }
        end
        gauge.len = nil
        gauge.fill = 1
        rows[#rows + 1] = {
          type = "box",
          axis = "horizontal",
          children = { text({ span(" " .. widgets.truncate(name, label_width - 2), theme.secondary) }, 1), gauge },
        }
        rows[#rows].children[1].len = label_width
      end
    end
  end
  if #rows == 0 then
    return text({ span(" Fuel unavailable · reading", theme.warn) }, 2)
  end
  -- A bounded header; the shared narrow pane retains every provider/window.
  local children = {}
  for i = 1, math.min(4, #rows), 2 do
    local cells = { rows[i] }
    if rows[i + 1] then
      cells[2] = rows[i + 1]
    end
    for _, cell in ipairs(cells) do
      cell.len = nil
      cell.fill = 1
    end
    children[#children + 1] = { type = "box", axis = "horizontal", len = 1, gap = 1, children = cells }
  end
  return { type = "box", len = #children, children = children }
end
local function detail(ctx)
  local key = "fleetboard-detail:" .. lead.id .. ":" .. state.board_detail
  run(
    key,
    "uv run --frozen --quiet fleet queue show -- " .. state.board_detail,
    { session = lead.id, ttl = 10, timeout = 15 }
  )
  local answer = (thurbox.runs or {})[key]
  local lines = {}
  local content = (not answer or answer.state == "pending") and "Reading record…"
    or answer.state == "failed" and "Record could not be read"
    or answer.stdout
    or "Reading record…"
  if type(content) ~= "string" then
    content = "Reading record…"
  end
  local width = math.max(1, ctx.width - 4)
  for original in (content .. "\n"):gmatch("(.-)\n") do
    local row, room = {}, width
    local function flush()
      lines[#lines + 1] = row
      row, room = {}, width
    end
    for _, run in ipairs(linked(original)) do
      local remaining = run.text
      while remaining ~= "" do
        local part = widgets.keep_left(remaining, room)
        if part == "" and #row > 0 then
          flush()
        else
          -- Even a viewport narrower than one wide glyph must make progress.
          if part == "" then
            part = remaining:sub(1, (utf8.offset(remaining, 2) or (#remaining + 1)) - 1)
          end
          row[#row + 1] = { text = part, style = run.style, role = run.role }
          remaining = remaining:sub(#part + 1)
          room = room - widgets.len(part)
          if room <= 0 then
            flush()
          end
        end
      end
    end
    if #row > 0 or original == "" then
      flush()
    end
  end
  local room = math.max(1, ctx.height - 5)
  state.board_detail_offset = math.max(0, math.min(state.board_detail_offset or 0, math.max(0, #lines - room)))
  local children = { text({ chip("Back", "close", false), span("  Up/down scroll · Esc goes back", theme.muted) }) }
  for i = state.board_detail_offset + 1, math.min(#lines, state.board_detail_offset + room) do
    children[#children + 1] = text(lines[i])
  end
  local root = ui.panel({ title = "Detail · " .. state.board_detail, body = children })
  root.float = { width = 100, height = 100 }
  return root
end
local picker_cache = {}
local function picker_rows(field)
  local query = field.value or ""
  if picker_cache.topics == topics and picker_cache.query == query then
    return picker_cache.rows
  end
  local fuzzy = require("lib.fuzzy")
  local needle = fuzzy.compile(query)
  local rows = {}
  if query == "" then
    rows[1] = { slug = "*", title = "All topics", count = #topics }
  end
  for _, topic in ipairs(topics) do
    if query == "" or fuzzy.match(needle, topic.title .. " " .. topic.slug) then
      rows[#rows + 1] = topic
    end
  end
  if query ~= "" then
    local ranked = {}
    for i, row in ipairs(rows) do
      local exact = row.slug:lower() == query:lower() or row.title:lower() == query:lower()
      ranked[row] = { exact = exact, order = i }
    end
    table.sort(rows, function(a, b)
      if ranked[a].exact ~= ranked[b].exact then
        return ranked[a].exact
      end
      return ranked[a].order < ranked[b].order
    end)
  end
  picker_cache = { topics = topics, query = query, rows = rows }
  return rows
end
local function pick_topic(row)
  if not row then
    return
  end
  state.board_topic = row.slug ~= "*" and row.slug or nil
  state.board_ref = nil
  state.board_picker = nil
end
local function topic_picker(ctx)
  local textinput = require("lib.textinput")
  local picker = state.board_picker
  local rows = picker_rows(picker.field)
  local controls_width = math.min(32, math.floor(ctx.width / 3))
  local width = math.max(1, ctx.width - controls_width - 5)
  local height = 6
  picker.cursor = math.max(1, math.min(#rows, picker.cursor))
  state.board_picker = picker
  local list = ui.list({
    items = rows,
    width = width - 2,
    height = height,
    pad = true,
    cursor = picker.cursor,
    on_overflow = "border",
    id_of = function(row)
      return "board-topic:" .. row.slug
    end,
    row = function(row)
      local b = ui.row({ width = width - 2 })
      b:add(" " .. widgets.truncate(row.title, math.max(1, width - 30)), { fg = theme.text, bold = true })
      if row.slug ~= "*" then
        b:add(" · " .. widgets.truncate(row.slug, 16), { fg = theme.muted })
      end
      b:trailing(row.count .. (row.slug == "*" and " topics" or " tasks"), { fg = theme.secondary })
      return b:spans_list()
    end,
  })
  list.len = height
  local controls = {
    type = "box",
    len = controls_width,
    children = {
      textinput.node(
        picker.field,
        { label = "Search topics", placeholder = "Name or slug…", focused = true, id = "board-topic-search" }
      ),
      text({
        span(#rows > 0 and (picker.cursor .. "/" .. #rows .. " · ↑/↓ or wheel") or "0 matches", theme.muted),
      }),
      text({ chip("All topics", "topic_all", false) }),
      text({
        #rows > 0 and chip("Select", "enter", false) or span("No matches", theme.muted),
        span(" "),
        chip("Cancel", "close", false),
      }),
    },
  }
  local body = {
    type = "box",
    axis = "horizontal",
    gap = 1,
    len = height,
    children = {
      controls,
      {
        type = "box",
        fill = 1,
        children = {
          #rows > 0 and list or text({ span(" No matching topics · edit your search", theme.muted) }, height),
        },
      },
    },
  }
  local panel = ui.panel({ title = "Choose topic · type to search", focused = true, body = { body } })
  panel.len = height + 2
  return panel
end
local function picker_action(name)
  local textinput = require("lib.textinput")
  local picker = state.board_picker
  local rows = picker_rows(picker.field)
  if name == "close" then
    state.board_picker = nil
  elseif name == "topic_all" then
    pick_topic({ slug = "*" })
  elseif name == "enter" then
    pick_topic(rows[math.max(1, math.min(#rows, picker.cursor))])
  elseif name == "up" or name == "down" then
    picker.cursor = math.max(1, math.min(#rows, picker.cursor + (name == "up" and -1 or 1)))
    state.board_picker = picker
  else
    -- Catalog-bound letters still type into the picker, like native search.
    local char = ({ topic = "t", agent = "a", needs = "n", landed = "l", detail = "d", fuel = "f" })[name]
    if not textinput.key(picker.field, { key = char or name, char = char }) then
      return false
    end
    if char then
      picker.cursor = 1
    end
    state.board_picker = picker
  end
  return true
end
function M.render(ctx, model, fuel, worker, fleet)
  lead = worker
  local now = (thurbox.taken_at_ms or widgets.now_ms()) / 1000
  local filter = table.concat(
    { state.board_topic or "", state.board_agent or "", tostring(state.board_needs), tostring(math.floor(now / 60)) },
    "\t"
  )
  if cached_model ~= model or cached_filter ~= filter then
    cached_model, cached_filter = model, filter
    columns = { {}, {}, {}, {}, {}, {} }
    topics = {}
    agents = {}
    locations = {}
    heights_by_column = { {}, {}, {}, {}, {}, {} }
    local seen = {}
    for ti, topic in ipairs(model.topics) do
      topics[#topics + 1] = { slug = topic.slug, title = topic.title, count = #topic.tasks }
      for _, task in ipairs(topic.tasks) do
        task.ref = topic.slug .. "/" .. task.id
        task.topic_index = ti
        task.agent, task.host, task.session, task.review, task.publish_detail =
          task.agent or "", task.host or "", task.session or "", task.review or "", task.publish_detail or ""
        if task.agent ~= "" and not seen[task.agent] then
          agents[#agents + 1] = task.agent
          seen[task.agent] = true
        end
        local c = category(task, now)
        if
          c
          and (not state.board_topic or state.board_topic == topic.slug)
          and (not state.board_agent or state.board_agent == task.agent)
          and (not state.board_needs or needs(task))
        then
          columns[c][#columns[c] + 1] = task
          locations[task.ref] = { column = c, index = #columns[c] }
          heights_by_column[c][#columns[c]] = 5
        end
      end
    end
  end
  selected = nil
  local location = state.board_ref and locations[state.board_ref]
  if location and (location.column ~= 6 or state.board_landed) then
    choose(location.column, location.index)
  end
  if not selected then
    for c, cards in ipairs(columns) do
      if #cards > 0 and (c ~= 6 or state.board_landed) then
        choose(c, 1)
        break
      end
    end
  end
  if state.board_detail then
    return detail(ctx)
  end
  if ctx.width < 80 or ctx.height < 28 then
    return ui.modal({
      title = "Fleet board",
      cols = math.min(60, ctx.width),
      rows = 7,
      children = { text({ span(" Resize to 80×28 or larger · Esc closes", theme.text) }) },
    })
  end
  local width = ctx.width - 2
  local fuel_view = state.board_fuel and fuel_node(fuel, width) or { type = "box", len = 0, children = {} }
  local strip_height = state.board_picker and 8 or 6
  local board_height = ctx.height - 5 - strip_height - fuel_view.len
  local per = ctx.width >= 180 and 6 or 3
  geometry = { now = now }
  local bands = {}
  for band = 0, 6 / per - 1 do
    local height = math.floor(board_height / (6 / per))
    if band == 6 / per - 1 then
      height = height + board_height % (6 / per)
    end
    local nodes = {}
    for x = 1, per do
      local cw = math.floor((width - per + 1) / per)
      if x == per then
        cw = width - per + 1 - (per - 1) * cw
      end
      nodes[#nodes + 1] = column_node(band * per + x, cw, height, ctx.elapsed)
    end
    bands[#bands + 1] = { type = "box", axis = "horizontal", gap = 1, len = height, children = nodes }
  end
  local health = model.health or "unknown"
  local header = text({
    span(" FLEET " .. (fleet ~= "" and fleet .. " · " or "") .. "Kanban", theme.accent),
    span("   reconciler " .. health, health == "ticking" and theme.ok or theme.warn),
    span(
      "   records updated " .. (model.read_at and math.max(0, math.floor(now - model.read_at)) .. "s ago" or "unknown"),
      theme.muted
    ),
  })
  local filters = text({
    chip(
      "topic: " .. (state.board_topic or "all"),
      state.board_picker and "close" or "topic",
      state.board_topic ~= nil or state.board_picker ~= nil
    ),
    span(" "),
    chip("agent: " .. (state.board_agent or "all"), "agent", state.board_agent ~= nil),
    span(" "),
    chip("needs me: " .. (state.board_needs and "on" or "off"), "needs", state.board_needs),
    span(" "),
    chip("landed: " .. (state.board_landed and "open" or "folded"), "landed", state.board_landed),
    span(" "),
    chip("fuel: " .. (state.board_fuel and "shown" or "hidden"), "fuel", state.board_fuel),
  })
  if state.board_picker then
    for _, run in ipairs(filters.text[1]) do
      if run.role and run.role ~= "action:fleetqueue.board_close" then
        run.role = nil
        run.style = { fg = theme.muted }
      end
    end
  end
  local strip = { text({ span(selected and " " .. selected.title or " No matching cards", theme.text) }) }
  if selected then
    strip[#strip + 1] = { type = "text", len = 2, wrap = true, text = { linked(" " .. note(selected)) } }
    strip[#strip + 1] = text({
      chip("Record", "detail", false),
      span(" "),
      url(selected.artifact) and span("artifact", theme.accent, "url:" .. selected.artifact) or span(""),
      span(" "),
      url(selected.review) and span("review", theme.accent, "url:" .. selected.review) or span(""),
    })
  end
  local selected_panel = ui.panel({ title = selected and "Selected · " .. selected.ref or "Selected", body = strip })
  if state.board_picker then
    selected_panel = topic_picker(ctx)
  end
  selected_panel.len = strip_height
  local footer = ui.footer({
    cancel = "Close",
    actions = {
      { "fleetqueue.board_left", "column" },
      { "fleetqueue.board_down", "card" },
      { "fleetqueue.board_enter", "focus / record" },
      { "fleetqueue.board_detail", "record" },
      { "fleetqueue.board_topic", "topic" },
      { "fleetqueue.board_fuel", "fuel" },
      { "fleetqueue.board_close", "close" },
    },
  })
  if state.board_picker then
    footer = ui.footer({
      cancel = "Cancel",
      actions = {
        { "fleetqueue.board_down", "topic" },
        { "fleetqueue.board_enter", "select" },
        { "fleetqueue.board_close", "cancel" },
      },
    })
  end
  footer.len = 1
  local root = ui.panel({
    title = "Fleet board · " .. ui.chord("fleetqueue.board"),
    body = {
      header,
      fuel_view,
      filters,
      { type = "box", len = board_height, children = bands },
      selected_panel,
      footer,
    },
  })
  root.float = { width = 100, height = 100 }
  root.frame.style = { fg = theme.text, bg = theme.role("app_bg") }
  return root
end
local function cycle(values, current)
  if not current then
    return values[1]
  end
  for i, v in ipairs(values) do
    if v == current then
      return values[i + 1]
    end
  end
end
function M.invalidate()
  selected = nil
  columns, locations, heights_by_column = { {}, {}, {}, {}, {}, {} }, {}, {}
  cached_model, cached_filter = nil, nil
  geometry = {}
  state.board_detail = nil
  state.board_picker = nil
end
function M.on_action(action)
  local name = action:match("^fleetqueue%.board_(.+)$")
  if state.board_picker then
    return picker_action(name)
  end
  if name == "close" then
    if state.board_detail then
      state.board_detail = nil
    else
      state.board_open = false
    end
  elseif name == "topic_all" then
    state.board_topic = nil
    state.board_ref = nil
  elseif name == "topic" then
    local field = require("lib.textinput").new("")
    local cursor = 1
    for i, topic in ipairs(topics) do
      if topic.slug == state.board_topic then
        cursor = i + 1
        break
      end
    end
    state.board_picker = { field = field, cursor = cursor }
  elseif name == "agent" then
    state.board_agent = cycle(agents, state.board_agent)
    state.board_ref = nil
  elseif name == "needs" then
    state.board_needs = not state.board_needs
    state.board_ref = nil
  elseif name == "fuel" then
    state.board_fuel = not state.board_fuel
  elseif name == "landed" then
    state.board_landed = not state.board_landed
  elseif name == "enter" or name == "detail" then
    if selected then
      local live = name == "enter" and session(selected)
      if live then
        command("action", { text = "session.focus", session = live.id })
        state.board_open = false
      elseif selected.ref:match("^[%w_.%-]+/[%w_.%-]+$") then
        state.board_detail = selected.ref
        state.board_detail_offset = 0
      end
    end
  elseif name == "up" or name == "down" then
    if state.board_detail then
      state.board_detail_offset = math.max(0, (state.board_detail_offset or 0) + (name == "up" and -1 or 1))
    else
      choose(state.board_column or 1, (state.board_index or 1) + (name == "up" and -1 or 1))
    end
  elseif name == "left" or name == "right" then
    local c = state.board_column or 1
    for _ = 1, 6 do
      c = (c - 1 + (name == "left" and -1 or 1)) % 6 + 1
      if #columns[c] > 0 and (c ~= 6 or state.board_landed) then
        choose(c, 1)
        break
      end
    end
  else
    return false
  end
  return true
end
function M.on_key(key)
  if state.board_picker then
    if key.key == "esc" or key.key == "enter" or key.key == "up" or key.key == "down" then
      return picker_action(key.key == "esc" and "close" or key.key)
    end
    local picker = state.board_picker
    local before = picker.field.value
    if not require("lib.textinput").key(picker.field, key) then
      return false
    end
    if picker.field.value ~= before then
      picker.cursor = 1
    end
    state.board_picker = picker
    return true
  end
  local name = ({
    esc = "close",
    enter = "enter",
    up = "up",
    down = "down",
    left = "left",
    right = "right",
    t = "topic",
    a = "agent",
    n = "needs",
    l = "landed",
    d = "detail",
    f = "fuel",
  })[key.key]
  return name and M.on_action("fleetqueue.board_" .. name) or false
end
function M.on_click(hit)
  if state.board_picker then
    if hit.id == "board-topic-search" then
      return true
    end
    local slug = (hit.id or ""):match("^board%-topic:(.+)$")
    for _, row in ipairs(picker_rows(state.board_picker.field)) do
      if row.slug == slug then
        pick_topic(row)
        return true
      end
    end
  end
  local ref = (hit.id or ""):match("^board:(.+)$")
  local location = ref and locations[ref]
  if location then
    state.board_picker = nil
    choose(location.column, location.index)
    return true
  end
  return false
end
function M.on_scroll(wheel)
  -- Native floats synthesize arrow keys for wheel ticks. Addressed scroll
  -- input follows the same selection, so the two paths cannot disagree.
  return M.on_action("fleetqueue.board_" .. (wheel.up and "up" or "down"))
end

return M
