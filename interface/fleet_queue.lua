-- The fleet task queue, in a column: the same view the web monitor serves.
--
-- WHY IT EXISTS. `scripts/webui.sh` already serves this queue as a local web
-- page, and that page is the better place to READ a brief. This pane is the
-- other half: the operator works in thurbox all day, and a monitor you have to
-- alt-tab to is a monitor you stop looking at. Same records, same information
-- architecture, no second model — `scripts/lib/webui.py`'s header owns both.
--
-- THE SAME FOUR THINGS PER TASK, AND NO FIFTH. The queue keeps four files per
-- task and they answer four questions: the PLAN (BRIEF.md), the PROGRESS
-- (progress.jsonl), the OUTCOME (result.md, distilled into `outcome`) and the
-- ARTIFACT (the pull request). This pane draws those four and the state word,
-- and invents nothing else — a monitor with a field of its own is a second
-- writer's opinion about a model it does not own.
--
-- READ-ONLY BY CONSTRUCTION. `focusable = false`, so the focus ring walks past
-- it and `ctrl+h`/`ctrl+l` never land here: it is a readout, not a place you go,
-- and there is no key on it that dispatches, collects or merges anything. Its
-- one action is the F-key that hides and shows its column — a global chord,
-- because a pane that cannot hold focus can only ever be reached by one. The
-- wheel scrolls it, since a pane that is never focused cannot be given a `j`.
--
-- WHERE THE QUEUE IS. This pane runs inside the thurbox interface, which knows
-- nothing about fleet, so it cannot guess the control-plane checkout — and it
-- must not, because `queue.sh` refuses to be run from a second clone for
-- exactly that reason. The one honest answer is `./scripts/queue.sh root`, run
-- in the checkout the `fleet` session opens. That session is what fleet's own
-- extension installs, so it is the same answer `queue.sh` itself would give.
--
-- IDENTITY IS THE CWD, NEVER `session.repo`. `session.repo` is the BASENAME of
-- a session's repository path — the lead's `~/fleet` and four worktrees of
-- `~/code/fleet` all call themselves "fleet". This pane picks the session by
-- NAME and reports `session.cwd`, so what it says it read is a directory rather
-- than a label several directories share.
--
-- NOT `pure`, deliberately. The bundled panes declare it and this one does not:
-- `run` re-asks from `render`, and a pure pane whose tree still stands is not
-- re-run at all — so on an otherwise idle screen the TTL below would never come
-- round again and the queue would freeze at whatever it last saw. What keeps the
-- cost down instead is that the parse is memoized on the output it parsed, and
-- that the visible rows are WINDOWED before any spans are built: a frame where
-- the probe said nothing new walks one already-built table and one slice of it.

local panels = require("lib.panels")
local theme = require("lib.theme")
local ui = require("lib.ui")
local widgets = require("lib.widgets")

local SLOT = "fleetqueue"
local TOGGLE = "fleetqueue.toggle"

--- The thurbox session that opens the control-plane checkout.
---
--- fleet's `extension.toml.in` names this session and thurbox self-heals it, so
--- it is a contract rather than a guess. Rename it there and rename it here.
local CONTROL_PLANE = "fleet"

--- Seconds an answer stays fresh.
---
--- The queue moves at the speed of pull requests: a task changes state when a
--- worker concludes or when the operator runs `collect`, which is minutes apart
--- at best. Ten seconds is fast enough that a task closing is noticed while you
--- are still looking, and slow enough that the probe costs one process every ten
--- seconds rather than one per frame.
local TTL = 10

--- A queue of a dozen topics is a few dozen small file reads, so a probe that
--- has not answered in this long is wedged rather than slow.
local TIMEOUT = 15

--- Rows the wheel moves.
local SCROLL_STEP = 3

--- One probe, answering with the whole queue in a line-per-record format.
---
--- WHY A SHELL PROBE AND NOT `files`. `files.read` is scoped to a session's root
--- and would need one read per file — a dozen topics is fifty round trips and a
--- YAML parser in Lua. One process every `TTL` that emits exactly the fields
--- this pane draws is less machinery in both places.
---
--- WHY IT NEVER RELIES ON AN EXIT STATUS. `queue.sh root` warns when it is run
--- outside the control-plane checkout, and a probe that read a non-zero exit as
--- "broken" would report an ordinary "nothing here" as a failure. So every
--- outcome this can distinguish is spelled on stdout as an `E` record and the
--- script exits 0; the reader below treats only a probe that could not RUN, or
--- that said nothing at all, as a failure.
---
--- The format is one record per line, tab-separated, because a tab is the one
--- character none of these fields carries:
---
---   R <queue root>
---   E <what went wrong>
---   T <topic slug> <topic title>
---   K <id> <state> <title> <outcome> <artifact> <blockers> <brief> <events> <result>
local PROBE = [==[
if [ ! -x ./scripts/queue.sh ]; then
  printf 'E\tnot the control-plane checkout\n'; exit 0
fi
root=$(./scripts/queue.sh root 2>/dev/null | grep '^/' | tail -1)
if [ -z "$root" ] || [ ! -d "$root" ]; then
  printf 'E\tqueue.sh could not name a queue directory\n'; exit 0
fi
printf 'R\t%s\n' "$root"
cd "$root" || exit 0
for topic in */; do
  topic=${topic%/}
  [ -f "$topic/topic.yaml" ] || continue
  printf 'T\t%s\t%s\n' "$topic" "$(sed -n 's/^title: *//p' "$topic/topic.yaml" | head -1)"
  for dir in "$topic"/*/; do
    [ -f "$dir/task.yaml" ] || continue
    events=0
    [ -f "$dir/progress.jsonl" ] && events=$(wc -l <"$dir/progress.jsonl" | tr -d ' ')
    brief=0; [ -f "$dir/BRIEF.md" ] && brief=1
    result=0; [ -f "$dir/result.md" ] && result=1
    awk -v b="$brief" -v p="$events" -v r="$result" '
      /^[a-z_]+: / { i = index($0, ": "); f[substr($0, 1, i - 1)] = substr($0, i + 2) }
      /^- task: / { n = n + 1; bl = bl (n > 1 ? "," : "") substr($0, 9) }
      END { printf "K\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
              f["id"], f["state"], f["title"], f["outcome"], f["artifact"],
              bl, b, p, r }
    ' "$dir/task.yaml"
  done
done
]==]

-- ── Reading the probe ──────────────────────────────────────────────────────

--- A YAML scalar with its quoting off, and `null` read as the absence it is.
local function scalar(value)
  value = value or ""
  local first, last = value:sub(1, 1), value:sub(-1)
  if #value >= 2 and first == last and (first == "'" or first == '"') then
    value = value:sub(2, -2)
  end
  if value == "null" or value == "~" then
    return ""
  end
  return value
end

--- One tab-separated record, split so an empty field stays a field.
local function split_tabs(line)
  local out = {}
  for part in (line .. "\t"):gmatch("(.-)\t") do
    out[#out + 1] = part
  end
  return out
end

--- The display state `queue.sh list` and the monitor both draw: a queued task
--- holding on a blocker reads as `waiting`, which is not a state on disk.
---
--- A blocker clears when the task it names is `landed` — the forge's answer that
--- the code is on `main`, not a worker's claim that it opened a pull request.
--- That rule is `queue.py`'s `blocker_cleared`; this is the same rule and not a
--- second opinion about it.
local function resolve_states(model)
  for _, topic in ipairs(model.topics) do
    for _, task in ipairs(topic.tasks) do
      local held = nil
      for ref in task.blockers:gmatch("[^,]+") do
        if model.state_of[ref] ~= "landed" then
          held = held or ref
        end
      end
      task.held_by = held
      task.ready = task.state == "queued" and held == nil
      if task.state == "queued" and held ~= nil then
        task.display_state = "waiting"
      else
        task.display_state = task.state
      end
    end
  end
end

--- Topic classification, ordered by what an operator should look at first.
--- `webui.py`'s `classify`, in Lua, with the same order and the same words.
local function classify(tasks)
  if #tasks == 0 then
    return "empty"
  end
  local ready, waiting, all_done = false, false, true
  for _, task in ipairs(tasks) do
    local s = task.display_state
    if s == "stuck" or s == "failed" then
      return "attention"
    end
    if s ~= "done" and s ~= "landed" and s ~= "abandoned" then
      all_done = false
    end
    if task.ready then
      ready = true
    end
    if s == "waiting" then
      waiting = true
    end
  end
  for _, task in ipairs(tasks) do
    if task.display_state == "dispatched" then
      return "running"
    end
  end
  if ready then
    return "ready"
  end
  if waiting then
    return "blocked"
  end
  if all_done then
    return "done"
  end
  return "ready"
end

local CLASS_ORDER = {
  attention = 1,
  running = 2,
  ready = 3,
  blocked = 4,
  done = 5,
  empty = 6,
}

--- The whole queue, out of one probe's stdout.
local function build_model(stdout)
  local model = { topics = {}, state_of = {}, counts = {} }
  local topic

  for line in (stdout .. "\n"):gmatch("(.-)\n") do
    local kind = line:sub(1, 1)
    if kind == "E" then
      model.error = split_tabs(line)[2]
    elseif kind == "R" then
      model.root = split_tabs(line)[2]
    elseif kind == "T" then
      local f = split_tabs(line)
      topic = { slug = f[2] or "", title = f[3] or "", tasks = {} }
      model.topics[#model.topics + 1] = topic
    elseif kind == "K" and topic then
      local f = split_tabs(line)
      local task = {
        id = f[2] or "",
        state = scalar(f[3]),
        title = scalar(f[4]),
        outcome = scalar(f[5]),
        artifact = scalar(f[6]),
        blockers = f[7] or "",
        brief = f[8] == "1",
        events = tonumber(f[9]) or 0,
        result = f[10] == "1",
      }
      topic.tasks[#topic.tasks + 1] = task
      model.state_of[topic.slug .. "/" .. task.id] = task.state
    end
  end

  resolve_states(model)
  for _, entry in ipairs(model.topics) do
    entry.class = classify(entry.tasks)
    -- SETTLED, which is not the same as the `done` classification. A task that
    -- is `done` has an OPEN pull request and the operator's next move is to
    -- review it — so its artifact row is the most useful row in the pane, and
    -- collapsing it would hide the one link worth clicking. Only `landed` (the
    -- forge says it merged) and `abandoned` leave nothing to look at.
    entry.settled = #entry.tasks > 0
    entry.landed = 0
    for _, task in ipairs(entry.tasks) do
      local s = task.display_state
      model.counts[s] = (model.counts[s] or 0) + 1
      if s == "landed" then
        entry.landed = entry.landed + 1
      elseif s ~= "abandoned" then
        entry.settled = false
      end
    end
  end
  table.sort(model.topics, function(a, b)
    local ra, rb = CLASS_ORDER[a.class] or 9, CLASS_ORDER[b.class] or 9
    if ra ~= rb then
      return ra < rb
    end
    -- Settled last WITHIN a class, which is the one ordering rule here the
    -- monitor does not have. It needs none: a web page can afford to draw a
    -- merged topic at full size. A column cannot, so the topics that collapsed
    -- to one row sink below the ones that did not, and `review-me` with an open
    -- pull request stops sitting under an archive of merged ones.
    if a.settled ~= b.settled then
      return b.settled
    end
    return a.slug < b.slug
  end)
  return model
end

--- `build_model`, done again only when the output actually changed.
---
--- The kernel keeps the previous answer readable while a refresh is in flight,
--- so most frames are handed a string this has already parsed — and this pane is
--- not `pure`, so most frames are frames it is asked on.
local parsed = { src = nil, model = nil }
local function model_for(stdout)
  if parsed.src == stdout then
    return parsed.model
  end
  parsed.src = stdout
  parsed.model = build_model(stdout)
  return parsed.model
end

-- ── Drawing ────────────────────────────────────────────────────────────────

--- One row. `role` is optional and carries a click verb when there is one.
local function line(spans, role)
  return { type = "text", len = 1, role = role, text = { spans } }
end

local function blank()
  return line({ { text = "" } })
end

--- The pane's frame, with the chord that hides it written into the title.
---
--- The hint is in the TITLE because an unfocusable pane is never visited by the
--- focus ring, so its keys never reach the footer's context hints. The chord is
--- read from the registry rather than spelled here, so a rebind moves it.
local function frame()
  local chord = ui.chord(TOGGLE)
  local title = { { text = " Fleet queue " } }
  if chord then
    title[#title + 1] = { text = chord:upper() .. " hides ", style = { fg = theme.muted } }
  end
  return {
    title = title,
    borders = "all",
    border_style = theme.muted,
    padding = 0,
  }
end

--- The pane with one thing to say in it.
---
--- A blank pane and a broken pane look identical, so every state that has no
--- rows still says which state it is.
local function saying(lines)
  local children = {}
  for _, sentence in ipairs(lines) do
    children[#children + 1] = line({ { text = "  " .. sentence, style = { fg = theme.muted } } })
  end
  return { type = "box", frame = frame(), children = children }
end

--- Glyph and role for one display state.
---
--- The distinction that earns the column: a dispatched task SPINS, so a worker
--- that is running does not look like one that finished an hour ago.
local function state_look(display, spinner)
  if display == "dispatched" then
    return spinner, theme.warn
  elseif display == "stuck" or display == "failed" then
    return "✗", theme.bad
  elseif display == "waiting" then
    return "◆", theme.secondary
  elseif display == "queued" then
    return "▶", theme.accent
  elseif display == "landed" then
    return "●", theme.info
  elseif display == "done" then
    return "●", theme.ok
  end
  return "○", theme.muted
end

--- Glyph and role for a topic's classification.
local function class_look(class, spinner)
  if class == "attention" then
    return "✗", theme.bad
  elseif class == "running" then
    return spinner, theme.warn
  elseif class == "ready" then
    return "▶", theme.accent
  elseif class == "blocked" then
    return "◆", theme.secondary
  elseif class == "done" then
    return "●", theme.ok
  end
  return "○", theme.muted
end

--- The rows the pane would draw, as descriptors rather than spans.
---
--- Built before anything is measured so the window can be taken out of it: a
--- queue of forty tasks costs forty small tables here and spans for only the
--- rows that land on screen.
---
--- A SETTLED topic — every task merged or abandoned — collapses to its heading.
--- It is the same judgement the monitor's ordering makes, what needs an operator
--- goes first, said in the one currency a column has, which is rows. A topic
--- whose tasks are merely `done` stays open on purpose: `done` means the pull
--- request is OPEN, so its artifact row is the row the operator came here to
--- click.
local function descriptors(model)
  local out = {}
  for _, topic in ipairs(model.topics) do
    out[#out + 1] = { kind = "topic", topic = topic }
    if not topic.settled and topic.class ~= "empty" then
      for index, task in ipairs(topic.tasks) do
        if index > 1 then
          out[#out + 1] = { kind = "blank" }
        end
        out[#out + 1] = { kind = "task", task = task }
        if task.held_by then
          out[#out + 1] = { kind = "blocker", task = task }
        end
        out[#out + 1] = { kind = "docs", task = task }
        if task.artifact ~= "" then
          out[#out + 1] = { kind = "artifact", task = task }
        end
      end
      out[#out + 1] = { kind = "blank" }
    end
  end
  return out
end

--- The four documents behind one task, on one row.
---
--- In the order the queue produces them — plan, progress, outcome — with the
--- artifact on its own row below because it is a link and links are clicked.
--- A missing plan is said out loud rather than left blank: `dispatch` refuses a
--- placeholder brief, so a task with no brief is a task that never went out.
local function docs_spans(task, width)
  local row = ui.row({ width = width })
  row:add("   ")
  if task.brief then
    row:add("plan", { fg = theme.muted })
  else
    row:add("no plan", { fg = theme.bad })
  end
  row:add(" · ", { fg = theme.muted })
  row:add(task.events .. (task.events == 1 and " event" or " events"), { fg = theme.muted })
  if task.outcome ~= "" then
    local tone = (task.outcome == "shipped" or task.outcome == "not-applicable") and theme.ok
      or theme.bad
    row:add(" · ", { fg = theme.muted })
    row:add(task.outcome, { fg = tone })
  elseif task.result then
    -- result.md is on disk but `collect` has not read it yet, which is a real
    -- and temporary state rather than "no outcome".
    row:add(" · ", { fg = theme.muted })
    row:add("uncollected", { fg = theme.warn })
  end
  return row:spans_list()
end

--- One descriptor, as the spans of a row.
local function draw(entry, width, spinner)
  if entry.kind == "blank" then
    return blank()
  end

  if entry.kind == "topic" then
    local topic = entry.topic
    local glyph, tone = class_look(topic.class, spinner)
    local row = ui.row({ width = width })
    row:add(" " .. glyph .. " ", { fg = tone })
    row:add(widgets.truncate(topic.slug, math.max(4, width - 14)), {
      fg = theme.accent,
      bold = true,
    })
    if topic.settled then
      local word = topic.landed == #topic.tasks and " landed" or " closed"
      row:trailing(#topic.tasks .. word, { fg = theme.muted })
    else
      row:trailing(topic.title, { fg = theme.muted })
    end
    return line(row:spans_list())
  end

  local task = entry.task
  if entry.kind == "task" then
    local glyph, tone = state_look(task.display_state, spinner)
    local word = task.display_state
    local row = ui.row({ width = width })
    row:add("   " .. glyph .. " ", { fg = tone })
    row:add(widgets.truncate(task.id, math.max(4, width - 8 - widgets.len(word))), {
      fg = theme.text,
      bold = true,
    })
    row:trailing(word, { fg = tone })
    return line(row:spans_list())
  end

  -- What a `waiting` task is waiting ON, on a row of its own.
  --
  -- It was the trailing note on the row above, and there it crushed the thing it
  -- was annotating: "waiting on 01-first" is twenty columns of a thirty-column
  -- row, and the task id it belongs to came out as `02-s…`. A blocker is also
  -- the one piece of state here a reader may want to type into `queue.sh`, so it
  -- is shown whole rather than budgeted against something else.
  if entry.kind == "blocker" then
    return line({
      { text = "     ↳ ", style = { fg = theme.secondary } },
      {
        text = widgets.truncate(task.held_by, math.max(4, width - 7)),
        style = { fg = theme.secondary },
      },
    })
  end

  if entry.kind == "docs" then
    return line(docs_spans(task, width))
  end

  -- The pull request, as a link rather than as text about a link. `url:` is the
  -- click verb the kernel also PAINTS: it re-prints the drawn cells wrapped in
  -- OSC 8, so the terminal thurbox runs in answers a Ctrl+Click on it. The role
  -- carries the whole url while the text carries what the column has room for,
  -- truncated in the MIDDLE because both ends identify a pull request — the host
  -- says which forge, the tail says which number.
  return line({
    { text = "     " },
    {
      text = widgets.middle_truncate(task.artifact, math.max(8, width - 5)),
      style = { fg = theme.accent, underline = true },
    },
  }, "url:" .. task.artifact)
end

--- The counters, in the monitor's own order and buckets: ready, running,
--- waiting, done, failed. `webui.py`'s HUD_GROUPS, and the same rule that every
--- display state lands in exactly one of them.
local HUD = {
  { label = "failed", role = "bad", states = { "stuck", "failed" } },
  { label = "running", role = "warn", states = { "dispatched" } },
  { label = "ready", role = "accent", states = { "queued" } },
  { label = "waiting", role = "secondary", states = { "waiting" } },
  { label = "done", role = "muted", states = { "done", "landed", "abandoned" } },
}

local function summary_spans(model, width)
  local row = ui.row({ width = width })
  local drew = false
  for _, group in ipairs(HUD) do
    local total = 0
    for _, name in ipairs(group.states) do
      total = total + (model.counts[name] or 0)
    end
    if total > 0 then
      row:add((drew and "  " or " ") .. total .. " " .. group.label, {
        fg = theme[group.role],
        bold = group.role ~= "muted",
      })
      drew = true
    end
  end
  if not drew then
    row:add(" no tasks", { fg = theme.muted })
  end
  return row:spans_list()
end

return {
  name = "fleetqueue",

  -- Placed by `layout.lua` as a side column. Without that edit this file loads,
  -- declares its key, and draws nothing — which is what `thurbox-cli plugin
  -- check` fails on, and it prints the line to add.
  slot = SLOT,
  order = 80,

  -- The point of the pane: it is watched, never entered. There is no key here
  -- that writes to the queue, and no focus to type one into.
  focusable = false,

  -- Declaring it is not being granted it: settings (`Ctrl+,`) → `]` → `t`.
  capabilities = { "run" },

  keys = {
    {
      key = "f6",
      action = TOGGLE,
      desc = "hide or show the fleet queue column",
      -- Global, so it works from inside a focused terminal — and an F-key, so it
      -- does not take a `ctrl+<letter>` the agent in that terminal wants. It is
      -- also the way BACK: the pane is not drawn while its column is closed, but
      -- the binding is resolved from the registry rather than from what is on
      -- screen, so a hidden pane still answers it.
      scope = "global",
      group = "UI",
    },
  },

  commands = {
    { action = TOGGLE, desc = "hide or show the fleet queue column" },
  },

  render = function(ctx)
    if not run then
      return saying({
        "not trusted yet",
        "settings → Interface → t",
      })
    end

    -- The control-plane checkout, named by the session fleet's own extension
    -- installs. Its cwd is the identity — never `session.repo`, which is a
    -- basename several different checkouts share.
    local lead
    for _, session in ipairs(thurbox.sessions or {}) do
      if session.name == CONTROL_PLANE and session.cwd then
        lead = session
        break
      end
    end
    if not lead then
      return saying({
        "no '" .. CONTROL_PLANE .. "' session",
        "run ./scripts/install-extension.sh",
        "in your fleet checkout",
      })
    end
    if lead.status == "unreachable" then
      return saying({ "the " .. CONTROL_PLANE .. " session is unreachable" })
    end

    local width = math.max(12, (ctx.width or 30) - 2)
    local spinner = theme.spinner_frame(ctx.elapsed)

    -- Asked every frame on purpose: the TTL decides whether a process runs, and
    -- a fresh answer is a table lookup.
    local key = "fleetqueue:" .. lead.id
    run(key, PROBE, { session = lead.id, ttl = TTL, timeout = TIMEOUT })
    local answer = (thurbox.runs or {})[key]

    if not answer or answer.state == "pending" then
      return saying({ spinner .. " reading the queue…" })
    end
    if answer.state == "failed" or (answer.stdout or "") == "" then
      -- NOT `not answer.ok`. The probe spells every condition it can tell apart
      -- on stdout and exits 0, so a non-zero exit is an ordinary answer here —
      -- reading it as a failure is how a queue with nothing in it gets reported
      -- as a broken pane. Only a probe the kernel could not RUN, or one that
      -- said nothing at all, is a failure.
      return saying({ "the queue probe did not run", "in " .. lead.cwd })
    end

    local model = model_for(answer.stdout or "")
    if model.error then
      return saying({ model.error, lead.cwd })
    end
    if #model.topics == 0 then
      return saying({ "the queue is empty", model.root or lead.cwd })
    end

    local children = { line(summary_spans(model, width)), widgets.divider(width) }

    -- Window BEFORE building rows: the descriptors are cheap tables and only the
    -- slice that lands on screen is turned into spans.
    local rows = descriptors(model)
    local room = math.max(1, (ctx.height or 0) - #children - 1)
    -- One row goes to the "↑ above" note as soon as the offset moves, so the
    -- last screenful is one row shorter than the first. Budgeting for it here is
    -- what lets the bottom of the queue actually be reached: without the `+ 1`
    -- the final two rows stayed under a "↓ 2 more" that never went away.
    local max_offset = (#rows <= room) and 0 or math.max(0, #rows - room + 1)
    local offset = math.min(math.max(0, state.offset or 0), max_offset)
    if offset ~= state.offset then
      -- Clamped rather than left past the end, so a wheel that ran off the
      -- bottom does not have to be wound all the way back.
      state.offset = offset
    end

    if offset > 0 then
      children[#children + 1] = line({
        { text = "  ↑ " .. offset .. " above", style = { fg = theme.muted } },
      })
    end
    local drawn = 0
    local last = math.min(#rows, offset + room - (offset > 0 and 1 or 0))
    for index = offset + 1, last do
      children[#children + 1] = draw(rows[index], width, spinner)
      drawn = drawn + 1
    end
    -- Rows past the bottom would be clipped with nothing said about it, which is
    -- the one way a pane like this can lie. The count takes over the last row it
    -- drew, so the tally includes the row it displaced.
    if last < #rows then
      local hidden = #rows - last + (drawn > 0 and 1 or 0)
      local note = line({
        { text = "  ↓ " .. hidden .. " more", style = { fg = theme.muted } },
      })
      if drawn > 0 then
        children[#children] = note
      else
        children[#children + 1] = note
      end
    end

    return { type = "box", frame = frame(), children = children }
  end,

  -- The wheel, because a pane that never holds focus can never be given a `j`.
  -- Taking the tick here is what stops the kernel turning it into an `up`/`down`
  -- keystroke for whatever does have focus.
  on_scroll = function(wheel)
    local step = wheel.up and -SCROLL_STEP or SCROLL_STEP
    state.offset = math.max(0, (state.offset or 0) + step)
    return true
  end,

  on_action = function(action)
    if action == TOGGLE then
      panels.toggle(SLOT)
      return true
    end
    return false
  end,
}
