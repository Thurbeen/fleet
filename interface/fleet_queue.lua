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
-- in the checkout the Mission Control session opens. That session is what
-- fleet's own extension installs, so it is the same answer `queue.sh` itself
-- would give.
--
-- IDENTITY IS THE CWD, NEVER `session.repo`. `session.repo` is the BASENAME of
-- a session's repository path — the lead's `~/fleet` and four worktrees of
-- `~/code/fleet` all call themselves "fleet". This pane picks the session by
-- NAME and reports `session.cwd`, so what it says it read is a directory rather
-- than a label several directories share.
--
-- THE FUEL LINE IS THE SAME READING THE SCREEN PRINTS. `scripts/lib/
-- fleet_status.py`'s `probe_fuel` is the only place the account's remaining
-- window is read, and this pane asks it through `fleet-status.sh --fuel`
-- rather than running `quota-axi` itself — a second parse of a document this
-- file does not own is the "second writer's opinion" the paragraph above
-- rejects, and it would disagree with the screen the moment either side moved.
-- It draws what was MEASURED (percent, the binding window, when that window
-- comes back) and never quota-axi's `runway` or `projectedExhaustedAt`, which
-- FLEET.md forbids fleet from restating as its own.
--
-- AND IT IS THE ONE PROBE THAT COSTS THE NETWORK, so it has its own `FUEL_TTL`
-- minutes long instead of the queue's seconds: a pane that refetched it per
-- frame would burn the fuel it is reporting. What is drawn is therefore always
-- a CACHED reading, and it is drawn with its age for exactly that reason.
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
--- it is a contract rather than a guess. Rename it there and rename it here —
--- that file's RENAMING header lists this line as one of the four places the
--- session's name lives, and a rename that misses it leaves the pane hunting a
--- session nobody spawns.
local CONTROL_PLANE = "⌖ Mission Control"

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

--- Seconds the FUEL answer stays fresh, and it is minutes rather than seconds.
---
--- The queue is files on disk; the fuel reading is a network call to the
--- provider, made on this pane's behalf by the account whose window it is
--- measuring. At the queue's ten seconds that is 360 calls an hour to watch a
--- number that moves as fast as an agent can spend it — the pane would be
--- burning the fuel it is reporting. Five minutes is far finer than the
--- windows involved (the shortest quota-axi reports is five hours) and coarse
--- enough that the reading costs one process a screenful of work.
local FUEL_TTL = 300

--- quota-axi talks to the provider, so this waits longer than a file read
--- does. `probe_fuel` already gives up on it at 20 seconds; this is that plus
--- room for the process around it.
local FUEL_TIMEOUT = 30

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
---   A <archived topic count>
---   T <topic slug> <topic title>
---   K <id> <state> <title> <outcome> <artifact> <blockers> <brief> <events>
---     <result> <branch> <moved-at, epoch seconds>
---
--- `<blockers>` is `ref|kind` pairs, comma separated. The KIND travels with the
--- ref because it is the whole reason the edge exists: `queue.sh block` refuses
--- a blocker that names no kind, so an edge without one is not a thing fleet can
--- have recorded, and a tree that showed only refs would be hiding the answer to
--- the only question a reader has about it.
---
--- `moved-at` is resolved and converted by the SHELL rather than in Lua, for
--- two reasons. `os` does not exist inside a pane, so there is no date parsing
--- here; and the queue writes three timestamps whose precedence is a fact about
--- the record — concluded, else dispatched, else created — which belongs beside
--- the record rather than in a renderer.
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
archived=0
for topic in */; do
  topic=${topic%/}
  [ -f "$topic/topic.yaml" ] || continue
  # One more sed of the file this loop already reads for the title, and the
  # task directories below are never opened. That is the whole cost of the
  # filter here, and it is why the flag lives on topic.yaml: deciding what to
  # hide by reading every task.yaml would make the hidden case the expensive
  # one.
  if [ -n "$(sed -n 's/^archived: *//p' "$topic/topic.yaml" | head -1)" ]; then
    archived=$((archived + 1))
    continue
  fi
  printf 'T\t%s\t%s\n' "$topic" "$(sed -n 's/^title: *//p' "$topic/topic.yaml" | head -1)"
  for dir in "$topic"/*/; do
    [ -f "$dir/task.yaml" ] || continue
    events=0
    [ -f "$dir/progress.jsonl" ] && events=$(wc -l <"$dir/progress.jsonl" | tr -d ' ')
    brief=0; [ -f "$dir/BRIEF.md" ] && brief=1
    result=0; [ -f "$dir/result.md" ] && result=1
    moved=""
    for field in concluded_at dispatched_at created; do
      [ -n "$moved" ] && continue
      moved=$(sed -n "s/^$field: *'\(.*\)'$/\1/p" "$dir/task.yaml" | head -1)
    done
    at=0
    [ -n "$moved" ] && at=$(date -d "$moved" +%s 2>/dev/null || echo 0)
    awk -v b="$brief" -v p="$events" -v r="$result" -v at="$at" '
      /^[a-z_]+: / { i = index($0, ": "); f[substr($0, 1, i - 1)] = substr($0, i + 2) }
      /^- task: / { n = n + 1; refs[n] = substr($0, 9) }
      /^  kind: / { kinds[n] = substr($0, 9) }
      END {
        for (i = 1; i <= n; i++) bl = bl (i > 1 ? "," : "") refs[i] "|" kinds[i]
        printf "K\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
              f["id"], f["state"], f["title"], f["outcome"], f["artifact"],
              bl, b, p, r, f["branch"], at
      }
    ' "$dir/task.yaml"
  done
done
printf 'A\t%s\n' "$archived"
]==]

--- The fuel probe: the account's remaining window, asked of the one thing that
--- reads it.
---
--- `fleet-status.sh --fuel` is `probe_fuel()` alone, printed as one
--- `name<TAB>value` line per field — the format exists because a thurbox pane
--- is Lua with no JSON parser, and `--json` would collect the whole screen
--- (a `gh pr list` per repo in flight, a `thurbox-cli session list`) to answer
--- one number.
---
--- IT SPELLS ITS OWN FAILURE, like the queue probe above and for the same
--- reason: `unavailable` is a field the reader already knows how to draw, so a
--- checkout that has no such script answers in the same vocabulary the
--- provider does when it has no number.
local FUEL_PROBE = [==[
if [ ! -x ./scripts/fleet-status.sh ]; then
  printf 'unavailable\tnot the control-plane checkout\n'; exit 0
fi
./scripts/fleet-status.sh --fuel 2>/dev/null
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

--- The dependency edges one task records, as `ref|kind` pairs.
---
--- This is the ordering fleet DECIDED, and it is the one thing in the queue a
--- reader cannot reconstruct from anywhere else: `touches` overlap is reported
--- and holds nothing up, so an edge here means somebody wrote down a concrete
--- reason that independent progress was unsafe.
local function edges(field)
  local out = {}
  for pair in field:gmatch("[^,]+") do
    local ref, kind = pair:match("^(.-)|(.*)$")
    out[#out + 1] = { ref = ref or pair, kind = kind or "" }
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
      for _, edge in ipairs(task.blocked_by) do
        -- A blocker clears when the task it names is `landed` — the forge's
        -- answer that the code is on `main`, not a worker's claim that it
        -- opened a pull request. That rule is `queue.py`'s `blocker_cleared`;
        -- this is the same rule and not a second opinion about it.
        edge.cleared = model.state_of[edge.ref] == "landed"
        if not edge.cleared then
          held = held or edge.ref
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
  local model = { topics = {}, state_of = {}, counts = {}, per_class = {}, archived = 0 }
  local topic

  for line in (stdout .. "\n"):gmatch("(.-)\n") do
    local kind = line:sub(1, 1)
    if kind == "E" then
      model.error = split_tabs(line)[2]
    elseif kind == "R" then
      model.root = split_tabs(line)[2]
    elseif kind == "A" then
      model.archived = tonumber(split_tabs(line)[2]) or 0
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
        topic = topic.slug,
        blocked_by = edges(f[7] or ""),
        brief = f[8] == "1",
        events = tonumber(f[9]) or 0,
        result = f[10] == "1",
        branch = scalar(f[11]),
        moved_at = tonumber(f[12]) or 0,
      }
      topic.tasks[#topic.tasks + 1] = task
      model.state_of[topic.slug .. "/" .. task.id] = task.state
    end
  end

  resolve_states(model)
  for _, entry in ipairs(model.topics) do
    entry.class = classify(entry.tasks)
    -- Tasks per classification, so a heading can say how many rows it covers
    -- with the same unit the counter at the top of the pane uses.
    model.per_class[entry.class] = (model.per_class[entry.class] or 0) + #entry.tasks
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

--- The fuel record, as a table. One `name<TAB>value` line per field, and a
--- field that has no value is ABSENT rather than empty — so `remaining` being
--- nil is "nobody could tell", which is a different fact from 0%.
local function build_fuel(stdout)
  local out = {}
  for line in (stdout .. "\n"):gmatch("(.-)\n") do
    local name, value = line:match("^([a-z_]+)\t(.*)$")
    if name then
      out[name] = value
    end
  end
  return {
    unavailable = out.unavailable,
    remaining = tonumber(out.remaining),
    reserve = tonumber(out.reserve),
    limited_by = out.limited_by,
    resets_at = out.resets_at,
    stale = out.stale == "1",
    read_at = tonumber(out.read_at),
  }
end

--- `build_fuel`, done again only when the record actually changed. The queue
--- probe's memo, for the same reason: this pane is not `pure`, so it is asked
--- on frames where nothing has been refetched.
local fuel_parsed = { src = nil, fuel = nil }
local function fuel_for(stdout)
  if fuel_parsed.src ~= stdout then
    fuel_parsed.src = stdout
    fuel_parsed.fuel = build_fuel(stdout)
  end
  return fuel_parsed.fuel
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
local function saying(lines, width)
  local room = math.max(1, (width or 40) - 2)
  local children = {}
  for _, sentence in ipairs(lines) do
    -- Wrapped rather than cut: these sentences are the pane's whole content in
    -- the states that have no rows, and half of "settings → Interface → t" is
    -- not an instruction. A word wider than the column is truncated, because a
    -- path or a url routinely is one.
    local current = ""
    local function flush()
      if current ~= "" then
        children[#children + 1] =
          line({ { text = "  " .. current, style = { fg = theme.muted } } })
        current = ""
      end
    end
    for word in sentence:gmatch("%S+") do
      if widgets.len(word) > room then
        flush()
        children[#children + 1] = line({
          { text = "  " .. widgets.truncate(word, room), style = { fg = theme.muted } },
        })
      elseif current == "" then
        current = word
      elseif widgets.len(current) + 1 + widgets.len(word) <= room then
        current = current .. " " .. word
      else
        flush()
        current = word
      end
    end
    flush()
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
--- How long ago the task last moved, compactly.
---
--- `widgets.time_ago` is the interface's own formatter and it is used rather
--- than a private one so an age here reads exactly like an age anywhere else in
--- thurbox. The " ago" is dropped because in a column this narrow the word is
--- 4 columns saying what the position already says. A task with no timestamp,
--- or a snapshot with no instant, gets no age rather than a wrong one.
local function age_of(task)
  local now = widgets.now_ms()
  if (task.moved_at or 0) <= 0 or now <= 0 then
    return nil
  end
  return (widgets.time_ago(task.moved_at * 1000, now):gsub(" ago", ""))
end

--- The monitor's own classification vocabulary, in its own order. Spelling the
--- bucket out as a heading is what the web page gets for free from having a
--- whole screen: there, `attention` is red and at the top and that reads as a
--- group. In one column the group has to say its own name.
--- The four blocker kinds `queue.sh block` accepts, in words that fit a column.
--- The set is closed on purpose — queue.py's BLOCKER_KINDS — so an unknown one
--- is shown verbatim rather than mapped to something plausible.
local BLOCKER_KIND = {
  ["semantic-dependency"] = "consumes",
  ["shared-external-state"] = "shared state",
  ["incompatible-migration"] = "migration",
  other = "other",
}

local CLASS_LABEL = {
  attention = "ATTENTION",
  running = "RUNNING",
  ready = "READY",
  blocked = "BLOCKED",
  done = "DONE",
  empty = "EMPTY",
}

--- The rows the pane would draw, as descriptors rather than spans.
---
--- Built before anything is measured so the window can be taken out of it: a
--- queue of forty tasks costs forty small tables here and spans for only the
--- rows that land on screen.
---
--- THE SHAPE IS THE MONITOR'S, said in rows. Topics arrive already ordered by
--- classification, so a change of class opens a heading; each unsettled topic
--- opens a rule with its slug; and each task is its TITLE first, because that
--- is the only line that says what the work actually is. The record id, which
--- is what `queue.sh` takes, sits under it with the state and the age.
---
--- A SETTLED topic — every task merged or abandoned — collapses to one row. A
--- topic whose tasks are merely `done` stays open on purpose: `done` means the
--- pull request is OPEN, so its artifact row is the row worth clicking.
local function descriptors(model)
  local out = {}
  local class = nil
  for _, topic in ipairs(model.topics) do
    if topic.class ~= class then
      class = topic.class
      if #out > 0 and out[#out].kind ~= "blank" then
        out[#out + 1] = { kind = "blank" }
      end
      out[#out + 1] = { kind = "class", class = class, count = model.per_class[class] or 0 }
    end
    if topic.settled or topic.class == "empty" then
      out[#out + 1] = { kind = "settled", topic = topic }
    else
      out[#out + 1] = { kind = "topic", topic = topic }
      for _, task in ipairs(topic.tasks) do
        out[#out + 1] = { kind = "task", task = task }
        out[#out + 1] = { kind = "meta", task = task }
        for _, edge in ipairs(task.blocked_by) do
          out[#out + 1] = { kind = "blocker", task = task, edge = edge }
        end
        out[#out + 1] = { kind = "docs", task = task }
        if task.artifact ~= "" then
          out[#out + 1] = { kind = "artifact", task = task }
        end
      end
      -- Close an expanded topic. Its own rule opens it, but nothing marked the
      -- END of one, so a collapsed topic that followed a task read as another
      -- of that task's rows.
      out[#out + 1] = { kind = "blank" }
    end
  end
  return out
end

--- The four documents behind one task, on one row.
---
--- In the order the queue produces them — plan, progress, outcome — with the
--- artifact on its own row below because it is a link and links are clicked.
--- Each one is NAMED rather than implied: `brief` is BRIEF.md, `events` are
--- progress.jsonl's lines, and the last word is the outcome result.md carried.
--- A missing brief is said out loud, because `dispatch` refuses a placeholder
--- one, so a task without it is a task that never went out.
---
--- BUDGETED, because this row is the one that overflows. `brief · 0 events ·
--- uncollected` is 29 columns before its indent, and the column it lives in is
--- 26% of the terminal — so at any ordinary width the kernel clipped it and the
--- outcome, the most important word on the row, was the half that went.
---
--- So the row gives things up in a fixed order instead of being cut: the WORD
--- "events" first, then the count, then the brief marker, and the OUTCOME last,
--- because it is the only part a reader acts on. Truncation is the final
--- fallback, not the first response.
local function docs_spans(task, width)
  local budget = math.max(1, width - 3)

  local plan = task.brief and "brief" or "no brief"
  local plan_tone = task.brief and theme.muted or theme.bad

  local outcome, outcome_tone
  if task.outcome ~= "" then
    outcome = task.outcome
    outcome_tone = (task.outcome == "shipped" or task.outcome == "not-applicable")
        and theme.ok
      or theme.bad
  elseif task.result then
    -- result.md is on disk but `collect` has not read it yet, which is a real
    -- and temporary state rather than "no outcome".
    outcome = "uncollected"
    outcome_tone = theme.warn
  end

  --- The segments this row would carry at one level of detail.
  local function segments(level)
    local out = {}
    if level ~= "outcome" then
      out[#out + 1] = { text = plan, tone = plan_tone }
    end
    if level == "full" then
      out[#out + 1] = {
        text = task.events .. (task.events == 1 and " event" or " events"),
        tone = theme.muted,
      }
    elseif level == "short" then
      out[#out + 1] = { text = task.events .. " ev", tone = theme.muted }
    end
    if outcome then
      out[#out + 1] = { text = outcome, tone = outcome_tone }
    end
    return out
  end

  local function columns(list)
    local n = 0
    for index, seg in ipairs(list) do
      n = n + widgets.len(seg.text) + (index > 1 and 3 or 0)
    end
    return n
  end

  local chosen
  for _, level in ipairs({ "full", "short", "plain", "outcome" }) do
    local list = segments(level)
    if columns(list) <= budget then
      chosen = list
      break
    end
  end
  if not chosen then
    -- Narrower than the outcome word itself. Truncate that and nothing else.
    chosen = segments("outcome")
    if chosen[1] then
      chosen[1].text = widgets.truncate(chosen[1].text, budget)
    end
  end

  local row = ui.row({ width = width })
  row:add("   ")
  for index, seg in ipairs(chosen) do
    if index > 1 then
      row:add(" · ", { fg = theme.muted })
    end
    row:add(seg.text, { fg = seg.tone })
  end
  return row:spans_list()
end

--- One descriptor, as the spans of a row.
local function draw(entry, width, spinner)
  if entry.kind == "blank" then
    return blank()
  end

  -- The classification heading. Its own colour, and the number of TASKS under
  -- it rather than topics: "3" beside RUNNING should mean the same three the
  -- counter at the top of the pane is counting.
  if entry.kind == "class" then
    local glyph, tone = class_look(entry.class, spinner)
    local label = CLASS_LABEL[entry.class] or entry.class
    local row = ui.row({ width = width })
    row:add(" " .. glyph .. " ", { fg = tone })
    row:add(widgets.truncate(label, math.max(1, width - 3)), { fg = tone, bold = true })
    row:trailing(tostring(entry.count), { fg = theme.muted })
    return line(row:spans_list())
  end

  -- A settled topic: one row, because there is nothing left to do about it.
  if entry.kind == "settled" then
    local topic = entry.topic
    local word = topic.landed == #topic.tasks and " landed" or " closed"
    local row = ui.row({ width = width })
    row:add("   ")
    row:add(widgets.truncate(topic.slug, math.max(1, width - 14)), { fg = theme.ok })
    row:trailing(#topic.tasks .. word, { fg = theme.muted })
    return line(row:spans_list())
  end

  -- An open topic: a rule carrying its slug, so the tasks under it read as a
  -- group without spending a colour on each of them.
  if entry.kind == "topic" then
    local topic = entry.topic
    local lead = width >= 10 and " ── " or " "
    local label = widgets.truncate(topic.slug, math.max(1, width - widgets.len(lead) - 2))
    local used = widgets.len(lead) + widgets.len(label)
    local spans = {
      { text = lead, style = { fg = theme.muted } },
      { text = label, style = { fg = theme.accent, bold = true } },
    }
    if width > used then
      spans[#spans + 1] = {
        text = " " .. string.rep("─", math.max(0, width - used - 1)),
        style = { fg = theme.muted },
      }
    end
    return line(spans)
  end

  local task = entry.task

  -- The task's TITLE. This is the line the pane was missing: an id is a handle,
  -- a title is what the work IS, and the monitor leads with it for that reason.
  if entry.kind == "task" then
    local glyph, tone = state_look(task.display_state, spinner)
    local title = task.title ~= "" and task.title or task.id
    return line({
      { text = " " .. glyph .. " ", style = { fg = tone } },
      {
        text = widgets.truncate(title, math.max(1, width - 3)),
        style = { fg = theme.text, bold = true },
      },
    })
  end

  -- The handle, the state and the age on one row: what to type into `queue.sh`,
  -- what it is doing, and how long it has been doing it.
  if entry.kind == "meta" then
    local _, tone = state_look(task.display_state, spinner)
    local note = task.display_state
    local age = age_of(task)
    if age then
      note = note .. " " .. age
    end
    local row = ui.row({ width = width })
    row:add("   ")
    row:add(widgets.truncate(task.id, math.max(1, width - 5 - widgets.len(note))), {
      fg = theme.secondary,
    })
    row:trailing(note, { fg = tone })
    return line(row:spans_list())
  end

  if entry.kind == "docs" then
    return line(docs_spans(task, width))
  end

  -- One dependency edge, under the task that carries it: the ordering fleet
  -- decided, with the reason it recorded.
  --
  -- CLEARED EDGES ARE DRAWN TOO. Showing only what is still holding answers
  -- "why is this stuck" and silently drops "why was this ever ordered" — and a
  -- ready task whose blocker just landed is exactly the row an operator wants
  -- to see, because it explains why the task became ready.
  --
  -- The ref loses its topic when it names a sibling, since the rule above it
  -- already says which topic that is; a cross-topic edge keeps the whole ref,
  -- because that one is genuinely elsewhere.
  if entry.kind == "blocker" then
    local edge = entry.edge
    local ref = edge.ref
    local sibling = task.topic .. "/"
    if ref:sub(1, #sibling) == sibling then
      ref = ref:sub(#sibling + 1)
    end
    local glyph = edge.cleared and "✓" or "◆"
    local tone = edge.cleared and theme.ok or theme.secondary
    local kind = BLOCKER_KIND[edge.kind] or edge.kind
    -- The prefix is budgeted like everything else: below about a dozen columns
    -- the indent and the marks cost more than the ref they are annotating, so
    -- they go and the ref stays.
    local lead, mark = "   ↳ ", glyph .. " "
    if widgets.len(lead) + widgets.len(mark) + 1 > width then
      lead, mark = "", ""
    end
    local room = width - widgets.len(lead) - widgets.len(mark) - widgets.len(kind)
    local row = ui.row({ width = width })
    row:add(lead, { fg = theme.muted })
    row:add(mark, { fg = tone })
    row:add(widgets.truncate(ref, math.max(1, room - 2)), { fg = tone })
    row:trailing(kind, { fg = theme.muted })
    return line(row:spans_list())
  end

  -- The pull request, as a link rather than as text about a link. `url:` is the
  -- click verb the kernel also PAINTS: it re-prints the drawn cells wrapped in
  -- OSC 8, so the terminal thurbox runs in answers a Ctrl+Click on it. The role
  -- carries the whole url while the text carries what the column has room for,
  -- truncated in the MIDDLE because both ends identify a pull request — the host
  -- says which forge, the tail says which number.
  return line({
    { text = "   " },
    {
      text = widgets.middle_truncate(task.artifact, math.max(1, width - 3)),
      style = { fg = theme.accent, underline = true },
    },
  }, "url:" .. task.artifact)
end

--- The counters, in the monitor's own order and buckets: ready, running,
--- waiting, done, failed. `webui.py`'s HUD_GROUPS, and the same rule that every
--- display state lands in exactly one of them.
---
--- The glyph beside each is the one the classification heading below uses, so
--- the top of the pane and the groups under it speak with one vocabulary.
local HUD = {
  { label = "failed", glyph = "✗", role = "bad", states = { "stuck", "failed" } },
  { label = "running", glyph = "◐", role = "warn", states = { "dispatched" } },
  { label = "ready", glyph = "▶", role = "accent", states = { "queued" } },
  { label = "waiting", glyph = "◆", role = "secondary", states = { "waiting" } },
  { label = "done", glyph = "●", role = "muted", states = { "done", "landed", "abandoned" } },
}

--- The counter row, in the widest form that fits.
---
--- Budgeted for the same reason the documents row is: `1 failed  2 ready
--- 1 waiting  4 done` is 37 columns, and this pane's column is routinely thirty.
--- Clipped, it lost the buckets on the right — which are the ones that say work
--- is finished, so a busy queue looked like an idle one. Under pressure the
--- WORDS go and the glyphs stay, because the headings below name them anyway.
local function summary_spans(model, width)
  local shown = {}
  for _, group in ipairs(HUD) do
    local total = 0
    for _, name in ipairs(group.states) do
      total = total + (model.counts[name] or 0)
    end
    if total > 0 then
      shown[#shown + 1] = { group = group, total = total }
    end
  end

  local row = ui.row({ width = width })
  if #shown == 0 then
    row:add(" no tasks", { fg = theme.muted })
    return row:spans_list()
  end

  local function label_of(entry, long)
    if long then
      return entry.total .. " " .. entry.group.label
    end
    return entry.group.glyph .. entry.total
  end

  local function columns(list, long)
    local n = 0
    for index, entry in ipairs(list) do
      n = n + (index == 1 and 1 or 2) + widgets.len(label_of(entry, long))
    end
    return n
  end

  local long = columns(shown, true) <= width
  -- Still too wide even in glyphs. Drop buckets from the RIGHT, which is the
  -- order HUD is written in: what needs an operator survives, what is merely
  -- finished goes first.
  while #shown > 1 and columns(shown, long) > width do
    shown[#shown] = nil
  end

  for index, entry in ipairs(shown) do
    local lead = index == 1 and " " or "  "
    row:add(lead .. widgets.truncate(label_of(entry, long), math.max(1, width - 1)), {
      fg = theme[entry.group.role],
      bold = entry.group.role ~= "muted",
    })
  end
  return row:spans_list()
end

--- How old the cached reading is, said the way every other age in this pane is.
---
--- It is drawn because the reading is always cached — `FUEL_TTL` is minutes —
--- and a number with no age silently claims to be now. `read_at` arrives as
--- epoch seconds for this: a pane has no `os` and cannot parse an instant.
local function read_age(fuel)
  local now = widgets.now_ms()
  if (fuel.read_at or 0) <= 0 or now <= 0 then
    return nil
  end
  return (widgets.time_ago(fuel.read_at * 1000, now):gsub(" ago", ""))
end

--- An instant with detail taken off it — never a different instant.
---
--- `1` drops the seconds, `2` drops the year as well. Both are compactions of
--- what quota-axi said and not a re-reading of it: the offset or `Z` stays on,
--- because a reset time whose zone has been filed off is a wrong reset time.
--- The year goes last and goes safely — the longest window quota-axi reports
--- is a week, so a reset is always inside the year the reader is standing in.
local function compact_instant(iso, level)
  local out = iso
  if level >= 1 then
    local head, tail = out:match("^(.-T%d%d:%d%d):%d%d[%.%d]*(.*)$")
    if head then
      out = head .. tail
    end
  end
  if level >= 2 then
    out = (out:gsub("^%d%d%d%d%-", ""))
  end
  return out
end

--- What the detail row gives up as the column narrows, in order.
---
--- The reserve goes FIRST, because the row above it is already coloured
--- against the reserve — losing the number costs a reader the arithmetic, not
--- the verdict. The instant is compacted next and the word "resets" only after
--- that, and the binding window is the last thing standing: a reset with no
--- window named does not say what is resetting.
local FUEL_DETAIL = {
  { reserve = true, instant = 0, word = true },
  { reserve = false, instant = 0, word = true },
  { reserve = false, instant = 1, word = true },
  { reserve = false, instant = 2, word = true },
  { reserve = false, instant = 2, word = false },
  { reserve = false },
}

local function detail_segments(fuel, level)
  local segs = {}
  if level.reserve and fuel.reserve then
    segs[#segs + 1] = "reserve " .. fuel.reserve .. "%"
  end
  if (fuel.limited_by or "") ~= "" then
    segs[#segs + 1] = fuel.limited_by
  end
  if level.instant and (fuel.resets_at or "") ~= "" then
    local when = compact_instant(fuel.resets_at, level.instant)
    segs[#segs + 1] = level.word and ("resets " .. when) or when
  end
  return segs
end

--- The reading's colour, taken from the reserve the reading itself carries.
---
--- The threshold is not this file's to hold: FLEET.md's `## Fuel` section owns
--- it, `fleet_status.py` carries the same number, and it travels down with
--- every record — so the pane compares and never spells it. AT the reserve is
--- red as well as under it, because the rule that number stands for is "below
--- it you dispatch nothing new", and a reading sitting exactly on the floor is
--- not headroom to dispatch into.
local function fuel_tone(fuel)
  if not fuel.remaining or not fuel.reserve then
    return theme.muted
  end
  if fuel.remaining <= fuel.reserve then
    return theme.bad
  end
  return theme.ok
end

--- The fuel line: what the whole column below it is competing for.
---
--- Two rows rather than one, because the three facts the operator acts on —
--- how much is left, which window is binding, when that window comes back —
--- do not fit in a column that is routinely thirty cells wide, and the reset
--- instant is the one of the three that a single budgeted line would drop
--- first. `render_fuel` in `fleet_status.py` splits the same reading over a
--- head and a continuation for the same reason; this is that shape in a
--- narrower place.
local function fuel_rows(fuel, width, spinner)
  local head = " fuel "
  local room = math.max(1, width - widgets.len(head))

  --- The head row: a lead-in, one thing to say, and the age flush right.
  local function headline(body, body_style, note, note_style)
    local row = ui.row({ width = width })
    row:add(head, { fg = theme.muted })
    body = widgets.truncate(body, room)
    row:add(body, body_style)
    if note then
      local pad = width - widgets.len(head) - widgets.len(body) - widgets.len(note)
      if pad >= 2 then
        row:add(string.rep(" ", pad))
        row:add(note, note_style)
      end
    end
    return line(row:spans_list())
  end

  --- The muted second row, at the most detail that fits.
  local function detail(text)
    return line({
      { text = "   " .. widgets.truncate(text, math.max(1, width - 3)), style = { fg = theme.muted } },
    })
  end

  if not fuel then
    return { headline(spinner .. " reading", { fg = theme.muted }) }
  end

  local age = read_age(fuel)

  -- An unreadable reading is drawn as unreadable, with the reason the probe
  -- gave — never as a zero, which would read as a spent window rather than a
  -- missing one.
  if fuel.unavailable or not fuel.remaining then
    return {
      headline("unavailable", { fg = theme.warn }, age, { fg = theme.muted }),
      detail(fuel.unavailable or "no reading"),
    }
  end

  local note, note_style = age, { fg = theme.muted }
  if fuel.stale then
    -- quota-axi's own word for its reading, passed through rather than
    -- interpreted: it means the number is remembered, not just observed.
    note = age and (age .. " stale") or "stale"
    note_style = { fg = theme.warn }
  end

  local rows = {
    headline(fuel.remaining .. "%", { fg = fuel_tone(fuel), bold = true }, note, note_style),
  }

  -- The widest level that fits, and the narrowest one when none does — which
  -- `detail` then truncates, the same last resort the documents row takes.
  local budget = math.max(1, width - 3)
  local chosen
  for _, level in ipairs(FUEL_DETAIL) do
    local segs = detail_segments(fuel, level)
    if #segs == 0 then
      break
    end
    chosen = table.concat(segs, " · ")
    if widgets.len(chosen) <= budget then
      break
    end
  end
  if chosen then
    rows[#rows + 1] = detail(chosen)
  end
  return rows
end

return {
  name = "fleetqueue",

  -- Placed by `layout.lua` as a side column. Without that edit this file loads,
  -- declares its key, and draws nothing — which is what `thurbox-cli plugin
  -- check` fails on, and it prints the block to add.
  slot = SLOT,
  order = 80,

  -- The point of the pane: it is watched, never entered. There is no key here
  -- that writes to the queue, and no focus to type one into.
  focusable = false,

  -- Declaring it is not being granted it: settings (`Ctrl+,`) → `]` → `t`.
  capabilities = { "run" },

  keys = {
    {
      -- F3, and NOT F4 or F6. The kernel binds F1 Help, F4 Theme, F6
      -- Settings, F10 reload and F12 the perf HUD, and advertises four of
      -- them in the action band; F7, F8 and F9 belong to the bundled panes.
      -- A plugin-scoped chord does not outrank a kernel one, so on F6 this
      -- pane registered in the key registry, rendered "F6 hides" in its own
      -- title, and never saw the key — the collision is invisible from in
      -- here. F2, F3, F5 and F11 are unclaimed.
      --
      -- Nothing about the pane depends on this being F3. The title below
      -- resolves its own chord from the key registry with `ui.chord`, so
      -- rebinding the action in thurbox's settings moves both the binding and
      -- the hint that advertises it; nothing here spells a key twice.
      key = "f3",
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
    local width = math.max(4, (ctx.width or 30) - 2)

    if not run then
      return saying({
        "not trusted yet",
        "settings → Interface → t",
      }, width)
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
      }, width)
    end
    if lead.status == "unreachable" then
      return saying({ "the " .. CONTROL_PLANE .. " session is unreachable" }, width)
    end

    local spinner = theme.spinner_frame(ctx.elapsed)

    -- Asked every frame on purpose: the TTL decides whether a process runs, and
    -- a fresh answer is a table lookup.
    local key = "fleetqueue:" .. lead.id
    run(key, PROBE, { session = lead.id, ttl = TTL, timeout = TIMEOUT })
    local answer = (thurbox.runs or {})[key]

    -- Its own key and its own TTL, so the network call the fuel reading costs
    -- is made once every `FUEL_TTL` and never at the queue's cadence. Asked
    -- here for the same reason the queue probe is: the TTL decides whether a
    -- process runs, and a fresh answer is a table lookup.
    local fuel_key = "fleetfuel:" .. lead.id
    run(fuel_key, FUEL_PROBE, { session = lead.id, ttl = FUEL_TTL, timeout = FUEL_TIMEOUT })
    local fuel_answer = (thurbox.runs or {})[fuel_key]
    local fuel
    if fuel_answer and fuel_answer.state ~= "pending" then
      -- A probe that could not RUN is still a reading nobody could take, so it
      -- is drawn as one rather than left blank.
      if (fuel_answer.stdout or "") == "" then
        fuel = { unavailable = "the fuel probe did not run" }
      else
        fuel = fuel_for(fuel_answer.stdout)
      end
    end

    if not answer or answer.state == "pending" then
      return saying({ spinner .. " reading the queue…" }, width)
    end
    if answer.state == "failed" or (answer.stdout or "") == "" then
      -- NOT `not answer.ok`. The probe spells every condition it can tell apart
      -- on stdout and exits 0, so a non-zero exit is an ordinary answer here —
      -- reading it as a failure is how a queue with nothing in it gets reported
      -- as a broken pane. Only a probe the kernel could not RUN, or one that
      -- said nothing at all, is a failure.
      return saying({ "the queue probe did not run", "in " .. lead.cwd }, width)
    end

    local model = model_for(answer.stdout or "")
    if model.error then
      return saying({ model.error, lead.cwd }, width)
    end
    local archived = model.archived or 0
    if #model.topics == 0 then
      if archived > 0 then
        -- Not an empty queue: a queue whose every topic has finished. Saying
        -- "empty" here would be the one lie this pane is able to tell.
        return saying({
          archived .. " archived topic(s), nothing live",
          "queue.sh list --archived",
        }, width)
      end
      return saying({ "the queue is empty", model.root or lead.cwd }, width)
    end

    -- FUEL FIRST, above the counters and the topics both: it is the account
    -- window every row below it is competing for, and the reading that decides
    -- whether anything below it should be dispatched at all.
    local children = {}
    for _, row in ipairs(fuel_rows(fuel, width, spinner)) do
      children[#children + 1] = row
    end
    children[#children + 1] = widgets.divider(width)
    children[#children + 1] = line(summary_spans(model, width))
    children[#children + 1] = widgets.divider(width)
    -- Above the scroll window rather than in it: the count is the pane saying
    -- what it is NOT drawing, so a queue long enough to scroll is exactly the
    -- queue where it must not be the row that scrolls off.
    if archived > 0 then
      children[#children + 1] = line({
        { text = "  " .. archived .. " archived", style = { fg = theme.muted } },
      })
    end

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
      if drawn > 0 or offset > 0 then
        -- Replace, not append: at room == 1 the "↑ above" note already spent
        -- this frame's one annotation line, and drawn == 0 there means there
        -- is no row to swap out either — swap the up-note itself instead of
        -- pushing the frame one line past its rect.
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
