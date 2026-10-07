-- Shared live record and fuel reader for the legacy column and Kanban overlay.
-- Both views share parsers with per-view memos; neither writes queue records.
local theme = require("lib.theme")
local M = {}
--- The thurbox session that opens the control-plane checkout, WITHOUT its mark.
---
--- fleet's `extension.toml.in` names this session and thurbox self-heals it, so
--- it is a contract rather than a guess. Rename it there and rename it here —
--- that file's RENAMING header lists this line as one of the two places the
--- session's name lives, and a rename that misses it leaves the pane hunting a
--- session nobody spawns. `uv run fleet check pane` fails a rename that stops
--- at one of them.
---
--- THE GLYPH IS DELIBERATELY NOT HERE, and that is the difference between this
--- constant and `FUEL_GLYPH` below. The lead wears a mark because thurbox has
--- no per-session icon field, and WHICH mark is a setting the operator can turn
--- off (`orchestration/session-glyphs.example.conf`): `📡` by default, `⌖` when
--- it is off. A pane that spelled one of those would be a second copy of a
--- setting it does not own, and would report "no session" the day the operator
--- flipped it — the RENAMING header's partial-rename failure, reached without
--- anyone renaming anything. So the pane matches the NAME and treats the mark
--- as decoration, which is the one arrangement in which the setting can move
--- without this file moving with it.
local CONTROL_PLANE = "Mission Control"

--- What stands between the lead and the FLEET it belongs to, when one is named.
---
--- A machine may run several fleets — one clone each, each with a Mission
--- Control of its own — and `orchestration/fleet.conf` is where a fleet takes
--- its name. `fleet install-extension` renders it into the manifest, on this
--- side of this mark; the mark itself is chosen in the same file the lead's
--- glyph is, and for the same reasons: U+00B7 is one cell, needs no variation
--- selector, and is not a character a worker's imperative title reaches for.
---
--- This is the one string here that must agree with the renderer, which is why
--- `uv run fleet check pane` compares the two.
local FLEET_MARK = " · "

--- The fleet this session leads: its name, "" for a fleet that named none, or
--- nil when the session is not a lead at all.
---
--- The mark in front is bounded rather than free — one non-space token of at
--- most four bytes, which is one UTF-8 codepoint — because a worker's name is
--- an imperative sentence about its work and one of those can end in these
--- words. "🚀 Rename Mission Control" is a worker; "📡 Mission Control" is the
--- lead of an unnamed fleet, and "📡 Mission Control · acme" leads `acme`.
---
--- The fleet's own name is matched as the grammar that renders it — letters,
--- digits, `_` and `-` — so "🚀 Fix Mission Control · then ship it" is still a
--- worker.
local function fleet_of(name)
  local body = name
  local mark, rest = name:match("^(%S+) (.*)$")
  if
    mark
    and #mark <= 4
    and (rest == CONTROL_PLANE or rest:sub(1, #CONTROL_PLANE + #FLEET_MARK) == CONTROL_PLANE .. FLEET_MARK)
  then
    body = rest
  end
  if body == CONTROL_PLANE then
    return ""
  end
  return body:match("^" .. CONTROL_PLANE .. FLEET_MARK .. "([%w_-]+)$")
end

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

--- One probe, answering with the whole queue in a line-per-record format.
---
--- WHY A PROBE AND NOT `files`. `files.read` is scoped to a session's root
--- and would need one read per file — a dozen topics is fifty round trips and a
--- YAML parser in Lua. One process every `TTL` that emits exactly the fields
--- this pane draws is less machinery in both places.
---
--- WHY ONE COMMAND LINE AND NOT A SCRIPT. thurbox runs a probe through `sh -c`
--- on POSIX and `cmd /C` on Windows, so the only probe that runs on both is a
--- plain command both shells read the same way. `scripts/lib/pane_probe.py` is
--- that command, run in the checkout the lead session opens, and its docstring
--- owns the format this pane reads:
---
---   R <queue root>
---   E <what went wrong>
---   A <archived topic count>
---   T <topic slug> <topic title>
---   K <id> <state> <title> <outcome> <artifact> <blockers> <brief> <events>
---     <result> <branch> <moved-at, epoch seconds> <publish-method>
---     <publish-state> <publish-at, epoch seconds>
---
--- `<blockers>` is `ref|kind` pairs, comma separated. The KIND travels with the
--- ref because it is the whole reason the edge exists: `fleet queue block`
--- refuses a blocker that names no kind, so a tree that showed only refs would
--- be hiding the answer to the only question a reader has about it. A
--- CONDITION — a wait on something outside the queue — rides in the same field
--- with `!` in front of it, since a task ref can never begin with one.
---
--- WHY IT NEVER RELIES ON AN EXIT STATUS. The probe spells every outcome it can
--- tell apart on stdout as an `E` record and exits 0; the reader below treats
--- only a probe that could not RUN, or that said nothing at all, as a failure.
--- Timestamps arrive as epoch seconds, because `os` does not exist inside a
--- pane.
local PROBE = "uv run --frozen --quiet python scripts/lib/pane_probe.py"

--- The fuel probe: the account's remaining window, asked of the one thing that
--- reads it.
---
--- `fleet status --fuel` is `probe_fuel()` alone, printed as one
--- `name<TAB>value` line per field — the format exists because a thurbox pane
--- is Lua with no JSON parser, and `--json` would collect the whole screen
--- (a `gh pr list` per repo in flight, a `thurbox-cli session list`) to answer
--- one number.
---
--- A checkout where it cannot run says nothing on stdout, which the reader
--- below draws as a reading nobody could take rather than as a blank.
local FUEL_PROBE = "uv run --frozen --quiet fleet status --fuel"

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
---
--- A `!` LEADER IS THE OTHER FORM: `queue.py`'s CONDITION, a wait on something
--- outside the queue. It is kept as a flag rather than as a prefix left on the
--- text, because every reader below asks one question of it — this one does
--- not clear on its own — and none of them wants the punctuation.
---
--- A condition is free prose, so it is the one field here the writer routinely
--- QUOTES: any text holding `: ` comes back from the probe as a quoted YAML
--- scalar. It gets `scalar()` for that reason, exactly as every other field
--- off the probe does; a task ref never needs it.
local function edges(field)
  local out = {}
  for pair in field:gmatch("[^,]+") do
    local ref, kind = pair:match("^(.-)|(.*)$")
    ref = ref or pair
    local condition = ref:sub(1, 1) == "!"
    out[#out + 1] = {
      ref = condition and scalar(ref:sub(2)) or ref,
      kind = kind or "",
      condition = condition,
    }
  end
  return out
end

--- The display state `fleet queue list` draws too: a queued task holding on a
--- blocker reads as `waiting`, which is not a state on disk.
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
        --
        -- AND A CONDITION NEVER CLEARS. There is no upstream to look up, and
        -- nothing but `fleet queue block --clear` releases one — same rule,
        -- same place in `queue.py`.
        edge.cleared = not edge.condition and model.state_of[edge.ref] == "landed"
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
--- The only definition there is; nothing else in the repo derives it.
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
    elseif kind == "H" then
      local f = split_tabs(line)
      model.health, model.read_at = f[2], tonumber(f[3])
    elseif kind == "B" and topic and #topic.tasks > 0 then
      local f = split_tabs(line)
      local task = topic.tasks[#topic.tasks]
      if f[2] == topic.slug .. "/" .. task.id then
        task.agent, task.host, task.session = f[3] or "", f[4] or "", f[5] or ""
        task.review, task.publish_detail, task.threads = f[6] or "", f[7] or "", tonumber(f[8])
      end
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
        publish_method = scalar(f[13]),
        publish_state = scalar(f[14]),
        publish_at = tonumber(f[15]) or 0,
      }
      topic.tasks[#topic.tasks + 1] = task
      model.state_of[topic.slug .. "/" .. task.id] = task.state
    end
  end

  resolve_states(model)
  for _, entry in ipairs(model.topics) do
    entry.class = classify(entry.tasks)
    -- TOPICS per classification, because a classification is a property of a
    -- topic and never of a task. This used to count tasks, which read as the
    -- same unit the counter row at the top of the pane uses and was not the
    -- same population: `4 running` above `RUNNING 5` is one dispatched task
    -- short and one landed task long, and both numbers were right. The two
    -- cannot be reconciled — a `dispatched` task can sit under an `attention`
    -- topic — so the heading names its unit instead of pretending to agree.
    model.per_class[entry.class] = (model.per_class[entry.class] or 0) + 1
    -- SETTLED, which is not the same as the `done` classification. A task that
    -- is `done` has an OPEN pull request and the operator's next move is to
    -- review it — so the row naming it (publish, or artifact for a record with
    -- no `publish.method`) is the most useful row in the pane, and collapsing
    -- it would hide the one link worth clicking. Only `landed` (the forge says
    -- it merged) and `abandoned` leave nothing to look at.
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
    -- Settled last WITHIN a class, because a column cannot afford to draw a
    -- merged topic at full size. The topics that collapsed to one row sink
    -- below the ones that did not, and `review-me` with an open pull request
    -- stops sitting under an archive of merged ones.
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
-- Each plugin has its own namespaced run answers. They can differ during a
-- refresh, so one view must not evict the other's unchanged snapshot.
local memos = {}
local function memo_for(view)
  view = view or "default"
  if not memos[view] then
    memos[view] = { queue = {}, fuel = {} }
  end
  return memos[view]
end
local function model_for(stdout, view)
  local parsed = memo_for(view).queue
  if parsed.raw == stdout then
    return parsed.model
  end
  -- Health/freshness changes repaint the header, not thousands of task tables.
  local health_row = stdout:match("^H\t([^\n]*)") or stdout:match("\nH\t([^\n]*)")
  local src = stdout:gsub("^H\t[^\n]*\n?", ""):gsub("\nH\t[^\n]*", "")
  if parsed.src ~= src then
    parsed.src = src
    parsed.model = build_model(src)
  end
  parsed.raw = stdout
  if health_row then
    local fields = split_tabs("H\t" .. health_row)
    parsed.model.health, parsed.model.read_at = fields[2], tonumber(fields[3])
  else
    parsed.model.health, parsed.model.read_at = nil, nil
  end
  return parsed.model
end

--- The fuel record, as a LIST of tables — one per provider.
---
--- The format is `name<TAB>value` lines with a BLANK LINE between records, and
--- a field that has no value is ABSENT rather than empty — so `remaining`
--- being nil is "nobody could tell", which is a different fact from 0%.
--- `render_fuel_record` in `fleet_status.py` owns that format and this is its
--- only reader; the blank line is what carries several subscriptions over a
--- wire that had one, and it keeps the reader a splitter rather than a parser.
---
--- Every record names itself with `provider`, so three readings can never be
--- drawn as one. A reading nobody could take at all — no quota-axi, no
--- credential anywhere — arrives as a single record with `unavailable` and no
--- provider, which is what a failed single-provider reading always looked like.
---
--- `account` is the OTHER half of that identity, and it arrives only when the
--- fleet has more than one: an account is a provider plus the login that
--- selects it, so two records reading `claude` under two logins are told apart
--- by nothing else. A fleet with one account sends no `account` field at all
--- and this draws exactly what it drew before there was a second.
---
--- `checkout` says which record is the checkout's OWN reading — `1` for it,
--- `0` for a named account — and travels beside `account` for the same reason:
--- a named account can be called the same as its own vendor, so comparing
--- `account` against `provider` to guess "is this the checkout's own" reads a
--- coincidence as a fact. `fuel_name` below reads this flag instead.
---
--- `window` is the one field that repeats: one line per window, as
--- `id<TAB>percent<TAB>reset epoch<TAB>label`, already in the order to draw.
local function build_fuel(stdout)
  local out, fields, windows = {}, nil, nil

  local function close()
    if fields then
      out[#out + 1] = {
        provider = fields.provider,
        account = fields.account,
        checkout = fields.checkout,
        unavailable = fields.unavailable,
        remaining = tonumber(fields.remaining),
        reserve = tonumber(fields.reserve),
        limited_by = fields.limited_by,
        stale = fields.stale == "1",
        read_at = tonumber(fields.read_at),
        windows = windows,
      }
      fields, windows = nil, nil
    end
  end

  for line in (stdout .. "\n"):gmatch("(.-)\n") do
    local name, value = line:match("^([a-z_]+)\t(.*)$")
    if name == "window" then
      fields, windows = fields or {}, windows or {}
      local id, pct, resets, label = value:match("^([^\t]*)\t([^\t]*)\t([^\t]*)\t?(.*)$")
      if id and tonumber(pct) then
        windows[#windows + 1] = {
          id = id,
          label = (label ~= "" and label) or id,
          remaining = tonumber(pct),
          resets_epoch = tonumber(resets),
        }
      end
    elseif name then
      fields = fields or {}
      fields[name] = value
    else
      -- Anything that is not a field ends the record, which makes the blank
      -- line a separator without making it a syntax: a stray line cannot
      -- silently merge two providers' readings into one.
      close()
    end
  end
  close()
  return out
end

--- `build_fuel`, done again only when the record actually changed. The queue
--- probe's memo, for the same reason: this pane is not `pure`, so it is asked
--- on frames where nothing has been refetched.
local function fuel_for(stdout, view)
  local fuel_parsed = memo_for(view).fuel
  if fuel_parsed.src ~= stdout then
    fuel_parsed.src = stdout
    fuel_parsed.fuel = build_fuel(stdout)
  end
  return fuel_parsed.fuel
end

local selected_fleet
function M.read(ctx, view)
  if not run then
    return { error = {
      "not trusted yet",
      "settings → Interface → t",
    } }
  end

  -- The control-plane checkout, named by the session fleet's own extension
  -- installs. Its cwd is the identity — never `session.repo`, which is a
  -- basename several different checkouts share.
  --
  -- THIS MACHINE'S LEADS FIRST. thurbox sets `host` on a session it reaches
  -- over ssh or wsl, and a lead mirrored from another host is an ordinary
  -- neighbour of the local one — often listed ahead of it. Binding to the
  -- first by name probed that host and drew its empty queue over live work
  -- here. So local leads win silently, and only when there is none does the
  -- first lead by name stand in.
  local remote
  local leads, seen = {}, {}
  for _, session in ipairs(thurbox.sessions or {}) do
    local fleet = session.cwd and fleet_of(session.name or "")
    if fleet then
      remote = remote or { session = session, fleet = fleet }
      if not session.host and not seen[session.cwd] then
        seen[session.cwd] = true
        leads[#leads + 1] = { session = session, fleet = fleet }
      end
    end
  end
  if #leads == 0 and remote then
    leads[1] = remote
  end
  if #leads == 0 then
    return {
      error = {
        "no " .. CONTROL_PLANE .. " session",
        "uv run fleet install-extension",
        "in your fleet checkout",
      },
    }
  end

  -- WHICH FLEET, when this machine runs several. Each is a checkout with a
  -- Mission Control of its own, and they are different queues — so the pane
  -- does not guess, and it does not ask twice either.
  --
  -- IT FOLLOWS THE SESSION LIST. `store.selected` is what that pane publishes
  -- and the agent pane already reads, so "the fleet you are looking at" is a
  -- question thurbox has answered already; a setting of the pane's own would
  -- be a second answer to keep in step, and one that goes stale the day a
  -- checkout moves.
  --
  -- AND IT REMEMBERS. Selecting a lead is how you choose a fleet; then you
  -- spend the day in WORKER sessions, which name no fleet and never could —
  -- a worker's cwd is its worktree of the target repo, with nothing in it
  -- that points back at the queue that dispatched it. A pane that fell back
  -- to the chooser on every worker would be a pane nobody could read, so the
  -- choice sticks to the cwd until another lead is selected. One fleet needs
  -- none of this and is drawn without being chosen.
  local chosen = leads[1]
  if #leads > 1 then
    chosen = nil
    local selected = store and store.selected
    for _, entry in ipairs(leads) do
      if selected and entry.session.id == selected then
        chosen = entry
        selected_fleet = entry.session.cwd
      end
    end
    for _, entry in ipairs(leads) do
      if not chosen and entry.session.cwd == selected_fleet then
        chosen = entry
      end
    end
    if not chosen then
      local lines = { #leads .. " " .. CONTROL_PLANE .. " sessions here" }
      for _, entry in ipairs(leads) do
        lines[#lines + 1] = (entry.fleet ~= "" and entry.fleet .. "  " or "") .. entry.session.cwd
      end
      lines[#lines + 1] = "select one in the session list to draw its queue"
      return { error = lines }
    end
  end

  local lead, fleet = chosen.session, chosen.fleet
  if lead.status == "unreachable" then
    return { error = { "the " .. CONTROL_PLANE .. " session is unreachable" } }
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
      fuel = { { unavailable = "the fuel probe did not run" } }
    else
      fuel = fuel_for(fuel_answer.stdout, view)
    end
  end

  if not answer or answer.state == "pending" then
    return { error = { spinner .. " reading the queue…" }, lead = lead, fleet = fleet }
  end
  if answer.state == "failed" or (answer.stdout or "") == "" then
    -- NOT `not answer.ok`. The probe spells every condition it can tell apart
    -- on stdout and exits 0, so a non-zero exit is an ordinary answer here —
    -- reading it as a failure is how a queue with nothing in it gets reported
    -- as a broken pane. Only a probe the kernel could not RUN, or one that
    -- said nothing at all, is a failure.
    return { error = { "the queue probe did not run", "in " .. lead.cwd }, lead = lead, fleet = fleet }
  end

  local model = model_for(answer.stdout or "", view)
  if model.error then
    return { error = { model.error, lead.cwd }, lead = lead, fleet = fleet }
  end
  return { model = model, fuel = fuel, lead = lead, fleet = fleet, spinner = spinner }
end
M.model_for = function(stdout)
  return model_for(stdout)
end
M.FLEET_MARK = FLEET_MARK
M.FUEL_TTL, M.FUEL_TIMEOUT = FUEL_TTL, FUEL_TIMEOUT
return M
