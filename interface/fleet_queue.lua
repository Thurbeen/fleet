-- The fleet task queue, in a column: the same view the web monitor serves.
--
-- WHY IT EXISTS. `scripts/webui.sh` already serves this queue as a local web
-- page, and that page is the better place to READ a brief. This pane is the
-- other half: the operator works in thurbox all day, and a monitor you have to
-- alt-tab to is a monitor you stop looking at. Same records, same information
-- architecture, no second model — `scripts/lib/webui.py`'s header owns both.
--
-- A GLANCE, NOT A RECORD. This pane answers ONE question — what is the fleet
-- working on right now — and every row it draws is a row competing with that
-- answer. It used to spend four rows per task plus one per dependency, all at
-- the same weight, and the operator's report on the result is the whole reason
-- this section exists: "custom pane displays too many elements, it is not
-- clear what we are really working on." Thirty-eight rows for eight tasks, and
-- the running ones looked exactly like the merged ones.
--
-- Forty columns, eight tasks, and the two topics with a worker in them. Over
-- the whole pane THIRTY-EIGHT rows became TWENTY-THREE, and in the second
-- the three bold rows are the three workers.
--
--   BEFORE
--    ◐ RUNNING  7
--    ── pane-declutter ─────────────────────
--    ◐ Cut the pane back to what the operat…
--      01-declutter-the-pane  dispatched 6m
--      ↳ ◆ publish-agnostic/03-dr…  consumes
--      brief · 3 events
--
--    ── publish-agnostic ───────────────────
--    ● Declare the publish method on the ta…
--      01-declare-publish-method  landed 24m
--      brief · 14 events · shipped
--      ⇡ no-mistakes · #43 · merged      20m
--    ◐ Record the publish state the shepher…
--      02-shepherd-records-…  dispatched 15m
--      ↳ ✓ 01-declare-publish-met…  consumes
--      brief
--    ◆ Draw the publish row in the TUI queu…
--      03-draw-publish-row  waiting 15m
--      ↳ ◆ 02-shepherd-records-pu…  consumes
--      brief
--
--   AFTER
--    ◐ RUNNING  3 topics
--    ── pane-declutter ─────────────────────
--    ◐ 01 Cut the pane back to what the…  6m
--
--    ── publish-agnostic ───────────────────
--    ◐ 02 Record the publish state the…  15m
--    ◆ 03 Draw the publish row in the …  15m
--      ↳ 02-shepherd-records-publ…  consumes
--      1 landed
--
-- WHAT WENT, AND WHERE IT WENT. Nothing here was deleted from fleet. Every one
-- of these is still printed by `queue.sh show`, which says of itself that the
-- record "keeps what a status line has stopped showing" — so the test each row
-- had to pass was not "is this available elsewhere" but "would the operator
-- act on it, at a glance, from this pane".
--
--   THE ID ROW        a kebab-case restatement of the title beside it. The
--                     task's NUMBER survives, on the title's own row; the full
--                     id is what `queue.sh list` prints and takes.
--   THE STATE WORD    the glyph already IS the state, and spins for a running
--                     worker; the heading above the topic names it as well.
--                     Collapsing what is finished (below) is what makes the
--                     glyph a complete encoding: ◐ ▶ ◆ ● ✗ are five distinct
--                     marks for the five states an open topic can now draw.
--   A CLEARED BLOCKER history. An UNCLEARED one is the reason a task is not
--                     moving and stays loud — and it is drawn only under a
--                     task that is actually `waiting`, because `queue.py`
--                     holds a `queued` task and no other, so an edge recorded
--                     against a running or concluded task holds nothing.
--   THE EVENT COUNT   `0 events` was under most tasks and said nothing about
--                     any of them. A raw count is not a thing an operator
--                     acts on, and `show` prints it with the LAST transition,
--                     which is the half that would be worth reading.
--   `brief`           `dispatch` refuses a placeholder brief, so a marker
--                     saying one exists is a constant. `no brief` is not one,
--                     and is still drawn, still red.
--   `shipped`         the same fact as the `done` glyph on the row above it.
--                     An outcome that DISAGREES with the state is drawn
--                     instead, which is `queue.py`'s own `state_conflict`.
--
-- FINISHED WORK WEIGHS LESS, WHEREVER IT SITS. A `landed` task inside a
-- RUNNING topic was drawn at full size, competing for attention with the work
-- it had already finished. It collapses into one muted `n landed` row under
-- its topic. What is merely `done` keeps its rows, because `done` means the
-- pull request is OPEN and reviewing it is the operator's next move. And only
-- a RUNNING task's title is bold, which is what makes the answer to this
-- pane's one question the thing you see first.
--
-- THE TWO TALLIES COUNT DIFFERENT THINGS AND NOW SAY SO. The counter row
-- counts TASKS by state over the whole queue; a classification heading covers
-- TOPICS. They cannot be made equal — a `dispatched` task can sit under a
-- topic classified `attention` — so `4 running` above a bare `RUNNING 5` was
-- this pane contradicting itself in the same unit. The heading carries its
-- unit now, and the counter row stays the queue-wide answer.
--
-- THE FOUR DOCUMENTS ARE STILL THE ONLY MODEL, drawn only where they differ
-- from their default. The queue keeps four files per task and they answer four
-- questions: the PLAN (BRIEF.md), the PROGRESS (progress.jsonl), the OUTCOME
-- (result.md, distilled into `outcome`) and the ARTIFACT (the pull request).
-- This pane still invents nothing beyond them — a monitor with a field of its
-- own is a second writer's opinion about a model it does not own. What changed
-- is that a fact equal to its default now costs no row.
--
-- AND `scripts/pane-selftest.sh` IS WHAT KEEPS THAT TRUE. It renders this file
-- offline, with stubbed `lib.*` modules, and asserts every rule above at 44
-- columns and again at 30. `check.sh pane` runs it beside the greps that hold
-- the pane's wiring together, which could never see a row.
--
-- THE `⇡` ROW IS THE ARTIFACT'S STATE, WHICH IS THE FOURTH THING AND NOT A
-- FIFTH. `publish.method` on `task.yaml` says what a task must PRODUCE — a
-- pull request, a commit on the base branch — and `publish.state` says what
-- fleet last saw when it went and looked at that artifact. Both are written by
-- `collect`, `shepherd` and `reap`, which are the commands that do the looking.
-- So the row draws a field of the record, in the record's own words, and this
-- pane still calls nothing: no `gh`, no `queue.sh`, no network. `queue.sh show`
-- prints the same block and `queue.sh list` the same word, which is what keeps
-- three readers of one field from becoming three opinions about it.
--
-- AND IT REPLACES THE ARTIFACT ROW, WHICH IS WHY IT COSTS NOTHING. Four rows
-- per task plus one per dependency is what made this pane something you read
-- rather than glance at, so a fifth was not available. The publish row names
-- the artifact — `#44`, a short sha — and carries the same `url:` verb the
-- artifact row carried, so the pull request is still one Ctrl+Click away and no
-- longer spends a line repeating as a URL what the line above it just said.
-- A task with an artifact is one row SHORTER than it was before this row
-- existed, and no task is taller.
--
-- FOLLOW-UP, WRITTEN DOWN RATHER THAN DONE: the probe below should become
-- `queue.sh list --tsv`. That would make "this pane cannot disagree with
-- `list`" literal instead of argued, and it would drop a dozen `sed`/`awk`/
-- `date` processes per refresh for one Python process the probe already pays
-- for. It is the better long-term shape, and it was deliberately left out of
-- the change that added the publish row: that change is six lines of awk, and
-- this file was under live test when it was written.
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
-- THE FUEL ROWS ARE THE SAME READING THE SCREEN PRINTS. `scripts/lib/
-- fleet_status.py` is the only place the account's remaining windows are read,
-- and this pane asks it through `fleet-status.sh --fuel` rather than running
-- `quota-axi` itself — a second parse of a document this file does not own is
-- the "second writer's opinion" the paragraph above rejects, and it would
-- disagree with the screen the moment either side moved. It draws what was
-- MEASURED (percent, the binding window, when that window comes back) and
-- never quota-axi's `runway` or `projectedExhaustedAt`, which FLEET.md forbids
-- fleet from restating as its own.
--
-- ONE ROW PER SUBSCRIPTION THAT HAS A NUMBER, each with the provider's name, a
-- bar and its percentage. The account may hold several and they are separate
-- windows on separate clocks, so nothing here is summed across them and the
-- name is what keeps three readings from being read as one. The bar is a
-- SECOND encoding of the number beside it, coloured against the reserve that
-- arrives on the record and marked where that floor falls — never a
-- replacement for the number.
--
-- A provider that could NOT be read is not drawn: it has no bar and no number,
-- and the column belongs to the readings. The exception is nothing reading at
-- all, which the block says in its own head row — a fuel block that quietly
-- disappeared would read as "nothing to report" when it means "nobody could
-- tell". Every failure is named in full by `fleet-status.sh` either way.
--
-- THE ⛽ ON THE HEAD ROW IS A SETTING, AND IT IS `FUEL_GLYPH` BELOW. Set it to
-- nil and this pane draws exactly what it drew before the glyph existed. It is
-- a switch because U+26FD is East_Asian_Width WIDE — two terminal cells, not
-- one. Every width here is measured with `widgets.len`, the kernel's own
-- `unicode-width`, so the budgets count it correctly and the head row's ladder
-- still fits at thirty columns; but a font that draws it narrow or a
-- multiplexer that disagrees about its width shears every row below it, and
-- that is a property of the operator's terminal rather than of this file.
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

--- The mark on the fuel head row. **Set it to nil to turn the glyph off.**
---
--- IT IS TWO CELLS WIDE, not one: U+26FD is East_Asian_Width WIDE, and that is
--- the whole reason this is a setting rather than a decision. Every budget in
--- this pane measures with `widgets.len`, which is the kernel's own
--- `unicode-width` — the same table the painter lays out with — so the head
--- row already counts this as two and the ladder below still fits at thirty
--- columns. What no budget here can control is the far side: a terminal font
--- that draws it narrow, or a multiplexer that disagrees about its width,
--- shears every row after it. `extension.toml.in`'s glyph header describes the
--- same hazard for the lead session's own mark, and answers it by choosing a
--- one-cell glyph; a pane can offer the switch instead.
---
--- No variation selector and no colour font: the codepoint alone is drawn, in
--- whatever presentation the terminal already has. A selector would add a
--- zero-width character that some terminals count as one anyway, which is the
--- shearing this is trying to avoid.
local FUEL_GLYPH = "⛽"

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
--- `<events>` is the one field here nothing draws any more — the header above
--- argues why a raw count is not a glance — and it is still emitted because
--- renumbering fourteen positional fields to drop one is a worse trade than
--- one `wc -l` per task.
---
---   K <id> <state> <title> <outcome> <artifact> <blockers> <brief> <events>
---     <result> <branch> <moved-at, epoch seconds> <publish-method>
---     <publish-state> <publish-at, epoch seconds>
---
--- `<blockers>` is `ref|kind` pairs, comma separated. The KIND travels with the
--- ref because it is the whole reason the edge exists: `queue.sh block` refuses
--- a blocker that names no kind, so an edge without one is not a thing fleet can
--- have recorded, and a tree that showed only refs would be hiding the answer to
--- the only question a reader has about it.
---
--- The three `publish-*` fields come out of ONE NESTED BLOCK, parsed the way
--- `- task:` / `  kind:` already is: a flag set on `publish:` and cleared by the
--- next top-level key, with the two-space keys read while it is set. The flag is
--- what makes it correct — `shepherd`, `artifact_check`, `landing` and `reaped`
--- are blocks of the same shape on the same record, and `state:` and `at:` at
--- two spaces of indent belong to whichever of them is open. For the same reason
--- the publish block must never grow a key named `kind`: the blocker rule below
--- has no such flag and would eat it.
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
    # When fleet last LOOKED at this task's artifact, converted here for the
    # same reason `moved` is: a pane has no clock and no date parser. The sed
    # is ranged to the publish block because four other blocks on this record
    # carry an `at:` at the same indent, and an unranged match would return
    # whichever of them came first.
    looked=$(sed -n "/^publish:/,/^[^ ]/s/^  at: *//p" "$dir/task.yaml" | head -1 | tr -d "'\"")
    pat=0
    [ -n "$looked" ] && pat=$(date -d "$looked" +%s 2>/dev/null || echo 0)
    awk -v b="$brief" -v p="$events" -v r="$result" -v at="$at" -v pat="$pat" '
      /^[a-z_]+: / { i = index($0, ": "); f[substr($0, 1, i - 1)] = substr($0, i + 2) }
      /^[^ ]/ { pub = ($0 ~ /^publish:/) }
      pub && /^  method: / { pm = substr($0, 11) }
      pub && /^  state: / { ps = substr($0, 10) }
      /^- task: / { n = n + 1; refs[n] = substr($0, 9) }
      /^  kind: / { kinds[n] = substr($0, 9) }
      END {
        for (i = 1; i <= n; i++) bl = bl (i > 1 ? "," : "") refs[i] "|" kinds[i]
        printf "K\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
              f["id"], f["state"], f["title"], f["outcome"], f["artifact"],
              bl, b, p, r, f["branch"], at, pm, ps, pat
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
local function build_fuel(stdout)
  local out, fields = {}, nil

  local function close()
    if fields then
      out[#out + 1] = {
        provider = fields.provider,
        unavailable = fields.unavailable,
        remaining = tonumber(fields.remaining),
        reserve = tonumber(fields.reserve),
        limited_by = fields.limited_by,
        resets_at = fields.resets_at,
        stale = fields.stale == "1",
        read_at = tonumber(fields.read_at),
      }
      fields = nil
    end
  end

  for line in (stdout .. "\n"):gmatch("(.-)\n") do
    local name, value = line:match("^([a-z_]+)\t(.*)$")
    if name then
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

--- How long ago the task last moved, compactly.
---
--- `widgets.time_ago` is the interface's own formatter and it is used rather
--- than a private one so an age here reads exactly like an age anywhere else in
--- thurbox. The " ago" is dropped because in a column this narrow the word is
--- 4 columns saying what the position already says. A task with no timestamp,
--- or a snapshot with no instant, gets no age rather than a wrong one.
---
--- Two rows ask for one: the task's own last move, and — on the publish row —
--- when fleet last LOOKED at its artifact. They are different instants about
--- the same task, so the epoch is the argument and neither row reads the
--- other's field.
local function ago(at)
  local now = widgets.now_ms()
  if (at or 0) <= 0 or now <= 0 then
    return nil
  end
  return (widgets.time_ago(at * 1000, now):gsub(" ago", ""))
end

local function age_of(task)
  return ago(task.moved_at)
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

--- The publish states `queue.py` writes, in the words this pane says them in.
---
--- THE WORD IS THE RECORD'S WORD. `queue.sh show` prints `publish.state` and
--- `queue.sh list` puts it in its extra column; a pane-only synonym would turn
--- three readers of one field into three opinions about it. What happens here
--- is abbreviation and nothing else: `⟳` and `✗` say "running" and "failed" in
--- one cell each, and `conflict` and `changes` are the same words with their
--- tails off. It is not cosmetic — `changes-requested`, a number and an age do
--- not fit the thirty columns this pane routinely gets, and a row forced to
--- truncate its only load-bearing word says nothing at all.
---
--- `green` IS NOT THE OK COLOUR, AND IT IS NOT `ready`. It means every gate the
--- FORGE knows about holds and NOBODY VETTED IT: checks passed, the branch
--- merges, and the task's method was `pr` — so there is no attestation that
--- review, tests and lint ran on the head that would actually merge. `ready` is
--- that same forge answer WITH that proof, and it is the only one fleet merges
--- unattended. The two words look redundant and are not. Collapsing them —
--- here, or by repainting `green` with `theme.ok` because warn "looks like a
--- problem" — converts fleet's evidence-over-trust property back into trust,
--- silently, and it is the sharpest risk this row's design names. The note
--- beside it says whose job the merge is, and it is the first thing the ladder
--- below drops.
---
--- `open` is muted rather than ok on purpose: `collect` proved the pull request
--- exists and comes from this task's branch, and nothing has yet looked at its
--- checks. That is a fact, not a verdict, so it gets no colour that reads as one.
---
--- A state this table does not know is drawn verbatim and muted — the same rule
--- `BLOCKER_KIND` follows, and for the same reason: an unknown word is shown,
--- never mapped to a plausible one.
local PUBLISH_WORD = {
  unverified = { text = "UNVERIFIED", tone = "bad" },
  unknown = { text = "unknown", tone = "muted" },
  open = { text = "open", tone = "muted" },
  pushed = { text = "pushed ✓", tone = "ok" },
  draft = { text = "draft", tone = "warn" },
  ["checks-running"] = { text = "checks ⟳", tone = "warn" },
  ["checks-failed"] = { text = "checks ✗", tone = "bad" },
  conflicting = { text = "conflict", tone = "bad" },
  ["changes-requested"] = { text = "changes", tone = "bad" },
  unattested = { text = "unattested", tone = "bad" },
  ready = { text = "ready ✓", tone = "ok" },
  green = { text = "green", tone = "warn", note = "yours to merge" },
  merged = { text = "merged", tone = "ok" },
  closed = { text = "closed", tone = "muted" },
}

--- The mark on the publish row. One cell — U+21E1 is East_Asian_Width Neutral,
--- unlike the `⛽` above it — so it needs no off switch of its own.
local PUBLISH_GLYPH = "⇡"

--- What the publish row gives up as the column narrows, in order.
---
--- The same shape `note_spans` uses — drop in a fixed order, truncate last —
--- and the order is what each part is FOR. The NOTE goes first: it is a
--- sentence about whose job a merge is, and the coloured word already carries
--- the fact. The METHOD next, because it is a property of the task that never
--- changes and the pull request page says it anyway, while the STATE is the
--- part an operator acts on. Then the AGE, then the artifact REFERENCE — which
--- is a label for the link this row carries, and the link survives losing its
--- label. Then the glyph. The state word is the last thing standing, and it is
--- truncated only when the column is narrower than the word itself.
local PUBLISH_LADDER = {
  { note = true, method = true, ref = true, age = true, glyph = true },
  { method = true, ref = true, age = true, glyph = true },
  { ref = true, age = true, glyph = true },
  { ref = true, glyph = true },
  { glyph = true },
  {},
}

--- Where each outcome a worker may write agrees with the state on the record.
---
--- `queue.py`'s OUTCOME_STATES, mirrored the way `blocker_cleared` already is:
--- the rule is fleet's, not this pane's, and `queue.sh list` prints the same
--- disagreement through `task_notes`.
---
--- It is used here to say NOTHING. An outcome that agrees with the state is the
--- state said twice — `shipped` under a task already drawn with the `done`
--- glyph — and it was on almost every concluded task in the queue. What is left
--- is the two cases that are not the state: a `not-applicable` task, which
--- concluded and produced nothing, and a record whose two facts contradict.
local OUTCOME_STATES = {
  shipped = { done = true, landed = true },
  ["not-applicable"] = { done = true, landed = true },
  stuck = { stuck = true },
  failed = { failed = true },
}

--- The task's documents, reduced to what is not already true by default.
---
--- Returns nil for the ordinary task, which is the point: `brief · 0 events`
--- was drawn under nearly every row in the queue and is three facts none of
--- which an operator can act on. What survives is a task that never went out,
--- a result nothing has collected, a task that concluded with nothing to
--- produce, and a record that disagrees with itself.
local function notes_of(task)
  local out = {}
  if not task.brief then
    -- `dispatch` refuses a placeholder brief, so this is a task that never
    -- went out — which is why the absence is drawn and the presence is not.
    out[#out + 1] = { text = "no brief", tone = theme.bad }
  end
  if task.outcome ~= "" then
    local agree = OUTCOME_STATES[task.outcome]
    if agree == nil then
      -- A word `queue.py` does not know, shown verbatim — the rule
      -- `BLOCKER_KIND` and `PUBLISH_WORD` follow, and for the same reason.
      out[#out + 1] = { text = task.outcome, tone = theme.bad }
    elseif not agree[task.state] then
      out[#out + 1] = { text = task.state .. " ≠ " .. task.outcome, tone = theme.bad }
    elseif task.outcome == "not-applicable" then
      -- The one outcome the state cannot carry: `done` is where both `shipped`
      -- and `not-applicable` land, and they are different answers.
      out[#out + 1] = { text = task.outcome, tone = theme.muted }
    end
  elseif task.result then
    -- result.md is on disk and `collect` has not read it yet, which is a real
    -- and temporary state rather than "no outcome" — and it is the one note
    -- here that names a command the operator should run.
    out[#out + 1] = { text = "uncollected", tone = theme.warn }
  end
  return (#out > 0) and out or nil
end

--- Columns a title is not cut below. Under this the row has stopped saying
--- what the work is, which is the only thing it is there for, so the number
--- and the age give way instead.
local TITLE_MIN = 12

local CLASS_LABEL = {
  attention = "ATTENTION",
  running = "RUNNING",
  ready = "READY",
  blocked = "BLOCKED",
  done = "DONE",
  empty = "EMPTY",
}

--- The rows ONE task is worth, appended in place.
---
--- Its own function because the topic loop below reads as a list of decisions
--- about a topic, and a task's three conditional rows nested inside it read as
--- one long branch. `goto` would have been the other way out; a Lua version is
--- not something a pane gets to assume.
local function task_rows(out, task)
  local display = task.display_state
  out[#out + 1] = { kind = "task", task = task }

  -- ONLY UNDER A `waiting` TASK, and only the edges that still hold.
  -- `resolve_states` marks a task `waiting` exactly when a blocker of its own
  -- has not cleared, which is `queue.py`'s rule — so an edge recorded against
  -- a task in any other state is holding nothing, and a cleared edge is
  -- holding nothing anywhere. Drawing either says "here is why this is not
  -- moving" about a task that is moving, or about a reason that has expired.
  -- `queue.sh show` keeps every one of them.
  if display == "waiting" then
    for _, edge in ipairs(task.blocked_by) do
      if not edge.cleared then
        out[#out + 1] = { kind = "blocker", task = task, edge = edge }
      end
    end
  end

  if notes_of(task) then
    out[#out + 1] = { kind = "note", task = task }
  end

  -- The publish row REPLACES the artifact row rather than joining it. It names
  -- the artifact and carries its link, so drawing both would spend two lines
  -- on one pull request.
  --
  -- It is drawn only when it has something to report: an artifact to name, or
  -- a state some producer actually recorded. A record from before `publish`
  -- existed has neither and is unchanged, and so is a task whose publish has
  -- not started — "nothing yet" is what the absence of this row has always
  -- meant.
  if task.publish_method ~= "" and (task.publish_state ~= "" or task.artifact ~= "") then
    out[#out + 1] = { kind = "publish", task = task }
  elseif task.artifact ~= "" then
    out[#out + 1] = { kind = "artifact", task = task }
  end
end

--- The rows the pane would draw, as descriptors rather than spans.
---
--- Built before anything is measured so the window can be taken out of it: a
--- queue of forty tasks costs forty small tables here and spans for only the
--- rows that land on screen.
---
--- ONE ROW PER TASK, AND A SECOND ONLY WHEN THERE IS SOMETHING TO ADD. Topics
--- arrive already ordered by classification, so a change of class opens a
--- heading; each unsettled topic opens a rule with its slug; and each task is
--- ONE row — its title, its number, its state as a glyph, its age. Under it,
--- and only when each has something to say: the blockers still holding a
--- `waiting` task, a note about its documents, and the publish row.
---
--- A SETTLED topic — every task merged or abandoned — collapses to one row. A
--- topic whose tasks are merely `done` stays open on purpose: `done` means the
--- pull request is OPEN, so the row naming it — publish, or artifact for a
--- record with no `publish.method` — is the row worth clicking.
---
--- AND THE FINISHED TASKS INSIDE AN OPEN TOPIC COLLAPSE THE SAME WAY. A
--- `landed` task drawn at full size under the RUNNING heading is finished work
--- competing with running work; the whole set of them becomes one muted
--- `n landed` row at the foot of the topic. It is at the FOOT because the
--- running work is what the topic is being read for.
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
      local landed, abandoned = 0, 0
      for _, task in ipairs(topic.tasks) do
        -- COUNTED, NOT DRAWN. The forge has answered for this one; nothing
        -- about it changes what the operator does next, and its topic is still
        -- open only because something ELSE under it is unfinished.
        if task.display_state == "landed" then
          landed = landed + 1
        elseif task.display_state == "abandoned" then
          abandoned = abandoned + 1
        else
          task_rows(out, task)
        end
      end
      if landed + abandoned > 0 then
        out[#out + 1] = { kind = "finished", landed = landed, abandoned = abandoned }
      end
      -- Close an expanded topic. Its own rule opens it, but nothing marked the
      -- END of one, so a collapsed topic that followed a task read as another
      -- of that task's rows.
      out[#out + 1] = { kind = "blank" }
    end
  end
  return out
end

--- What one task's documents say, when they say anything at all.
---
--- `notes_of` decides WHETHER this row exists; this decides how much of it
--- fits. The row it replaces was `brief · 0 events · uncollected` under every
--- task in the queue — 29 columns, in a column that is 26% of the terminal, of
--- which the outcome was the only part anyone acted on and the part the kernel
--- clipped. The rule that fixed it is now upstream of the row: a fact equal to
--- its default is not drawn, so the common case is no row.
---
--- BUDGETED, in the ladder `PUBLISH_LADDER` uses and for the same reason: drop
--- in a fixed order, truncate last. There are at most two segments here and
--- the LAST is the one that names an outcome, so it is the one that survives.
local function note_spans(task, width)
  local budget = math.max(1, width - 3)
  local notes = notes_of(task) or {}

  local chosen = notes
  local function columns(list)
    local n = 0
    for index, seg in ipairs(list) do
      n = n + widgets.len(seg.text) + (index > 1 and 3 or 0)
    end
    return n
  end
  if columns(chosen) > budget and #chosen > 1 then
    chosen = { chosen[#chosen] }
  end

  local row = ui.row({ width = width })
  row:add("   ")
  for index, seg in ipairs(chosen) do
    if index > 1 then
      row:add(" · ", { fg = theme.muted })
    end
    row:add(widgets.truncate(seg.text, math.max(1, width - row.used)), { fg = seg.tone })
  end
  return row:spans_list()
end

--- The artifact, in the fewest columns that still identify it.
---
--- This is what the row absorbed the artifact ROW to say. A pull request is its
--- number and a `push` task's commit is a short sha — both are what a reader
--- would have read off the end of the URL anyway — and anything else keeps the
--- URL with its scheme off, because a shape this does not recognise is one it
--- must not pretend to summarise. The whole row carries the link either way, so
--- what is drawn here is a label for a click target rather than the target.
local function artifact_ref(artifact)
  if artifact == "" then
    return nil
  end
  local number = artifact:match("/pull/(%d+)")
  if number then
    return "#" .. number
  end
  local sha = artifact:match("/commit/(%x%x%x%x%x%x%x+)")
  if sha then
    return sha:sub(1, 7)
  end
  return (artifact:gsub("^https?://", ""))
end

--- What fleet last saw when it looked at this task's artifact, on one row.
---
--- THE PANE ADDS NO FACT HERE. Every part of it is read off `task.yaml`:
--- `publish.method`, `publish.state` and `publish.at`, written by `collect`,
--- `shepherd` and `reap`, plus the artifact URL the record already carries.
--- Nothing on this row calls `gh`, and there is no state here that `queue.sh
--- show` would not print in the same word.
---
--- IT ABSORBS THE ARTIFACT ROW RATHER THAN SITTING ABOVE ONE. A task already
--- spends four rows saying it exists — title, handle, documents, artifact — and
--- a fifth for every dependency it records, which is how a pane meant to show
--- what the fleet is working on became a pane you have to read. So this row
--- names the artifact itself (`#44`, a short sha) and `draw` hands the WHOLE row
--- the same `url:` verb the artifact row used to carry, which the kernel paints
--- as OSC 8: the link is still one Ctrl+Click away, and it no longer costs a
--- line of its own to say what this line already said. Net, a task with an
--- artifact is one row SHORTER than before this row existed.
---
--- WHICH IS ALSO WHY IT IS NOT DRAWN FOR "nothing has happened yet". A method
--- with no state and no artifact is a task whose publish has not started, and
--- `descriptors` skips it: a row per task saying so would spend the columns
--- this fold just recovered on the tasks that have the least to report.
---
--- COLOUR CARRIES THE VERDICT, which is the whole reason the row is worth a
--- line: ok for the states that mean the artifact arrived (`ready`, `pushed`,
--- `merged`), warn for the ones still in motion or still owed a human
--- (`checks ⟳`, `draft`, `green`), bad for the ones an operator has to do
--- something about, and muted for a fact with no verdict attached. The table
--- above owns which is which, and owns the argument for `green`.
---
--- THE AGE IS THE AGE OF THE LOOK, not of the task. The shepherd runs on the
--- lead's cadence rather than on a clock, so a `checks ⟳` recorded forty
--- minutes ago has to read forty minutes old — a state word with no age
--- silently claims to be now. A task nothing has looked at yet has no age,
--- because there is no instant to draw.
local function publish_spans(task, width)
  local budget = math.max(1, width - 3)
  local method = task.publish_method
  local body, tone, note, age

  if task.publish_state ~= "" then
    local word = PUBLISH_WORD[task.publish_state]
    body = word and word.text or task.publish_state
    tone = word and word.tone or "muted"
    note = word and word.note
    age = ago(task.publish_at)
  else
    -- An artifact exists and nothing has recorded a verdict on it yet — a pull
    -- request `watch` linked before `collect` read the worker's result, most
    -- often. The method is the row's last-standing word, so the ladder still
    -- has something to keep at every rung.
    body, tone, method = method, "muted", nil
  end

  local ref = artifact_ref(task.artifact)

  --- The segments this row would carry at one rung of the ladder.
  local function segments(rung)
    local out = {}
    if rung.method and method and method ~= "" then
      out[#out + 1] = method
    end
    if rung.ref and ref then
      out[#out + 1] = ref
    end
    out[#out + 1] = (rung.note and note) and (body .. " — " .. note) or body
    return out
  end

  local function columns(list, rung)
    local n = rung.glyph and widgets.len(PUBLISH_GLYPH .. " ") or 0
    for index, seg in ipairs(list) do
      n = n + widgets.len(seg) + (index > 1 and 3 or 0)
    end
    if rung.age and age then
      -- Two columns of gap before it, which is what the flush below wants.
      n = n + 2 + widgets.len(age)
    end
    return n
  end

  local chosen, level
  for _, rung in ipairs(PUBLISH_LADDER) do
    local list = segments(rung)
    if columns(list, rung) <= budget then
      chosen, level = list, rung
      break
    end
  end
  if not chosen then
    -- Narrower than the state word itself. Truncate that and nothing else.
    level = PUBLISH_LADDER[#PUBLISH_LADDER]
    chosen = segments(level)
    chosen[#chosen] = widgets.truncate(chosen[#chosen], budget)
  end

  local row = ui.row({ width = width })
  row:add("   ")
  if level.glyph then
    row:add(PUBLISH_GLYPH .. " ", { fg = theme[tone] })
  end
  for index, seg in ipairs(chosen) do
    if index > 1 then
      row:add(" · ", { fg = theme.muted })
    end
    if index == #chosen then
      -- The STATE is the last segment and the only one that carries the
      -- verdict. What precedes it is context — which method, which pull
      -- request — and context in the verdict's colour would make every row
      -- shout.
      row:add(seg, { fg = theme[tone] })
    elseif seg == ref then
      -- The reference keeps the artifact row's own styling, because it is the
      -- artifact row: underlined accent is what said "this is a link" before
      -- the two rows became one, and the row is still the click target.
      row:add(seg, { fg = theme.accent, underline = true })
    else
      row:add(seg, { fg = theme.muted })
    end
  end
  if level.age and age then
    -- Pushed to the right edge, so the ages down the column line up and a row
    -- that has gone stale is visible without reading it.
    local pad = width - row.used - widgets.len(age)
    if pad >= 2 then
      row:add(string.rep(" ", pad))
      row:add(age, { fg = theme.muted })
    end
  end
  return row:spans_list()
end

--- One descriptor, as the spans of a row.
local function draw(entry, width, spinner)
  if entry.kind == "blank" then
    return blank()
  end

  -- The classification heading, with the number of TOPICS it covers AND the
  -- word "topics" on it. The bare number that used to sit here counted tasks,
  -- which is the counter row's unit over a different population, so the two
  -- disagreed in public and neither was wrong. Named, it is a second fact
  -- rather than a second opinion; `row:trailing` drops it where the column
  -- cannot afford it, which leaves the heading exactly as it was.
  if entry.kind == "class" then
    local glyph, tone = class_look(entry.class, spinner)
    local label = CLASS_LABEL[entry.class] or entry.class
    local row = ui.row({ width = width })
    row:add(" " .. glyph .. " ", { fg = tone })
    row:add(widgets.truncate(label, math.max(1, width - 3)), { fg = tone, bold = true })
    row:trailing(entry.count .. (entry.count == 1 and " topic" or " topics"),
      { fg = theme.muted })
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
  --
  -- NOT BOLD, though it was. Bold on this pane means one thing now — a worker
  -- is at work on this row — and a rule over every topic in the queue drowned
  -- it. The accent colour and the rule itself already separate the group.
  if entry.kind == "topic" then
    local topic = entry.topic
    local lead = width >= 10 and " ── " or " "
    local label = widgets.truncate(topic.slug, math.max(1, width - widgets.len(lead) - 2))
    local used = widgets.len(lead) + widgets.len(label)
    local spans = {
      { text = lead, style = { fg = theme.muted } },
      { text = label, style = { fg = theme.accent } },
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

  -- THE TASK, ON ONE ROW. Its state as a glyph, its number, what the work is,
  -- and how long it has been that way. It was two rows: a title, and under it
  -- the record id with the state word and the age. The id was a kebab-case
  -- restatement of the title directly above it and the state word was the
  -- glyph in longhand, so the second row cost a line per task to repeat the
  -- first — with eight tasks on screen that is eight lines of the answer to
  -- "what is the fleet working on" spent saying nothing new.
  --
  -- THE NUMBER STAYS AND THE SLUG GOES. `01` is what orders the topic's tasks
  -- and it is the part of the id a reader uses; the rest of the id is the
  -- title in kebab-case, and `queue.sh list` prints it in full for the one job
  -- it has left, which is being typed at `queue.sh show`.
  --
  -- ONLY A RUNNING TASK IS BOLD. That is the whole visual answer to this
  -- pane's one question: `dispatched` is a worker at work, `stuck` and
  -- `failed` are a worker that stopped, and everything else is context around
  -- them. It is what stops a merged task and a running one reading alike.
  --
  -- The age is flushed RIGHT, like the publish row's, so the ages line up down
  -- the column and a task that has gone quiet is visible without being read.
  -- It gives way to the title rather than the other way round: below
  -- `TITLE_MIN` the row has stopped saying what the work is.
  if entry.kind == "task" then
    local glyph, tone = state_look(task.display_state, spinner)
    local loud = task.display_state == "dispatched"
      or task.display_state == "stuck"
      or task.display_state == "failed"
    local title = task.title ~= "" and task.title or task.id
    local number = task.id:match("^(%d+)%-")
    local age = age_of(task)

    local row = ui.row({ width = width })
    row:add(" " .. glyph .. " ", { fg = tone })
    if number and width - row.used - widgets.len(number) - 1 >= TITLE_MIN then
      row:add(number .. " ", { fg = theme.muted })
    end
    local reserve = age and (widgets.len(age) + 2) or 0
    if width - row.used - reserve < TITLE_MIN then
      reserve, age = 0, nil
    end
    row:add(widgets.truncate(title, math.max(1, width - row.used - reserve)), {
      fg = loud and theme.text or theme.secondary,
      bold = loud,
    })
    if age then
      local pad = width - row.used - widgets.len(age)
      if pad >= 2 then
        row:add(string.rep(" ", pad))
        row:add(age, { fg = theme.muted })
      end
    end
    return line(row:spans_list())
  end

  -- The tasks of an open topic that the forge has already answered for, as one
  -- muted row at the foot of it. Same vocabulary as the settled-topic row
  -- above, because it is the same fact about a smaller set.
  if entry.kind == "finished" then
    local parts = {}
    if entry.landed > 0 then
      parts[#parts + 1] = entry.landed .. " landed"
    end
    if entry.abandoned > 0 then
      parts[#parts + 1] = entry.abandoned .. " abandoned"
    end
    local row = ui.row({ width = width })
    row:add("   ")
    row:add(widgets.truncate(table.concat(parts, " · "), math.max(1, width - 3)),
      { fg = theme.muted })
    return line(row:spans_list())
  end

  if entry.kind == "note" then
    return line(note_spans(task, width))
  end

  -- The publish row, carrying the artifact row's own click verb when there is
  -- an artifact: the kernel re-prints the drawn cells wrapped in OSC 8, so the
  -- whole row answers a Ctrl+Click and the pull request needs no line of its
  -- own to be reachable.
  if entry.kind == "publish" then
    local role = task.artifact ~= "" and ("url:" .. task.artifact) or nil
    return line(publish_spans(task, width), role)
  end

  -- THE ONE EDGE THAT IS STILL HOLDING THIS TASK, with the reason fleet
  -- recorded for it — which is the whole reason the edge exists, since
  -- `queue.sh block` refuses one that names no kind.
  --
  -- CLEARED EDGES ARE NOT DRAWN, and neither is any edge under a task that is
  -- not `waiting`. Both used to be: the argument was that a ready task whose
  -- blocker just landed explains why it became ready. It does, and that is
  -- history — the task is READY, the operator's next move is to dispatch it,
  -- and `✓` rows outnumbered `◆` rows on every screen this pane drew. `queue.sh
  -- show` prints every blocker "moot and cleared ones included", in its own
  -- words, and says there that the record keeps what a status line has stopped
  -- showing. This is that line.
  --
  -- Which is also why the mark went with them: with only holding edges left,
  -- `◆` marked every row it appeared on and cost two columns of the ref.
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
    local kind = BLOCKER_KIND[edge.kind] or edge.kind
    -- The prefix is budgeted like everything else: below about a dozen columns
    -- the indent and the arrow cost more than the ref they are annotating, so
    -- they go and the ref stays.
    local lead = "   ↳ "
    if widgets.len(lead) + 1 > width then
      lead = ""
    end
    local room = width - widgets.len(lead) - widgets.len(kind)
    local row = ui.row({ width = width })
    row:add(lead, { fg = theme.muted })
    row:add(widgets.truncate(ref, math.max(1, room - 2)), { fg = theme.secondary })
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
--- The reserve is no longer on this ladder: it is said once on the block's
--- head row, for every provider at once, and each bar marks where it falls —
--- so repeating it per reading would spend columns saying what the colour
--- already says. The instant is compacted first, then the word "resets", and
--- the binding window is the last thing standing: a reset with no window named
--- does not say what is resetting.
local FUEL_DETAIL = {
  { instant = 0, word = true },
  { instant = 1, word = true },
  { instant = 2, word = true },
  { instant = 2, word = false },
  {},
}

local function detail_segments(fuel, level)
  local segs = {}
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

--- The bar's four glyphs.
---
--- A fresh reading is drawn solid and a STALE one hatched, because the number
--- behind a stale bar is remembered rather than observed and a bar that looked
--- identical either way would be the one part of the row that hid it. The
--- reserve is a tick rather than a colour change, so it stays visible on both
--- sides of itself.
local BAR_FILLED, BAR_STALE, BAR_EMPTY, BAR_RESERVE = "█", "▒", "░", "┃"

--- The narrowest bar that still reads as a proportion. Under it the bar is
--- dropped and the number stands alone: a two-cell bar is a decoration wearing
--- the columns the reading itself could have used.
local FUEL_BAR_MIN = 5

--- Columns the percentage is given, so every bar in the block ends in the same
--- one. `100%` is the widest reading there is.
local FUEL_NUMBER = 4

--- Columns a provider's name may spend. Long enough for the names quota-axi
--- reports, short enough that the bar is still a bar at thirty cells.
local FUEL_LABEL_MAX = 8

--- The reading as a bar, with the reserve marked where it falls across it.
---
--- THE BAR IS A SECOND ENCODING OF THE NUMBER, never a replacement: it makes
--- "nearly gone" legible without reading, and the number beside it stays for
--- everything a glance cannot do.
---
--- THE FLOOR IS MARKED because it is what the colour is computed against.
--- `reserve N%` is the first thing the block gives up as it narrows, and a
--- tick on the bar hands that arithmetic back without spending a row on it.
--- The threshold itself is never spelled here — it rides in on the record.
local function bar_spans(fuel, cells)
  local filled = math.floor((fuel.remaining / 100) * cells + 0.5)
  filled = math.max(0, math.min(cells, filled))
  local mark
  if fuel.reserve then
    -- Clamped into the bar rather than off its end: a floor drawn nowhere is
    -- a floor the reader has to take on trust.
    mark = math.floor((fuel.reserve / 100) * cells + 0.5)
    mark = math.max(1, math.min(cells, mark))
  end

  local filled_style = { fg = fuel_tone(fuel) }
  local empty_style = { fg = theme.muted }
  local mark_style = { fg = theme.warn }
  local glyph = fuel.stale and BAR_STALE or BAR_FILLED

  -- Coalesced by style identity, so a bar is three spans rather than one per
  -- cell: this is rebuilt on every frame the pane is asked for.
  local spans, last = {}, nil
  for cell = 1, cells do
    local char, style = BAR_EMPTY, empty_style
    if cell == mark then
      char, style = BAR_RESERVE, mark_style
    elseif cell <= filled then
      char, style = glyph, filled_style
    end
    if last and last.style == style then
      last.text = last.text .. char
    else
      last = { text = char, style = style }
      spans[#spans + 1] = last
    end
  end
  return spans
end

--- The fuel block: one row per subscription, above everything competing for it.
---
--- ONE ROW PER PROVIDER THAT HAS A NUMBER, with its name, a bar and its
--- percentage. The name is not decoration: three subscriptions drawn without
--- one are three numbers that read as one reading with two mistakes in it.
---
--- A PROVIDER THAT COULD NOT BE READ IS NOT DRAWN. It has no bar to draw and
--- no number to compare, and a standing `unavailable` row for a provider the
--- operator is not spending is a row the queue below could have used. What it
--- could not say is still said in full by `./scripts/fleet-status.sh`, which
--- prints every provider with the reason its fetch failed.
---
--- UNLESS NOTHING READ AT ALL. Then the head row itself says `unavailable`
--- with the reason under it, because a fuel block that quietly disappeared
--- would read as "nothing to report" when it means "nobody could tell" — and
--- that is the one failure this pane must not commit silently.
---
--- WHAT A NARROW COLUMN DROPS, and this column is routinely thirty cells wide.
--- In order: the reserve on the head row, then the bar — under FUEL_BAR_MIN
--- cells it is a decoration and the number is the reading. The number never
--- goes.
---
--- WHAT SEVERAL SUBSCRIPTIONS DROP. One reading keeps the detail row it always
--- had: the binding window and when it comes back. Several do not, because N
--- readings at two rows each pushes the queue itself off the column, and
--- `./scripts/fleet-status.sh` is where every window is printed in full. So
--- the detail row is drawn only when exactly one provider carries a number —
--- which is still the common case, with the others unread rather than absent.
---
--- TWO READINGS ARE NOT BARS. No record yet is the spinner, and a stale
--- reading is hatched and flagged, so a remembered number never looks like a
--- freshly measured one.
local function fuel_rows(fuel, width, spinner)
  -- Measured, never counted: with the glyph on this is nine columns and not
  -- eight, and every budget below is taken from what it leaves. Clamped to
  -- `width` itself, because at the narrowest columns even this mandatory
  -- prefix does not fit whole.
  local lead = widgets.keep_left(FUEL_GLYPH and (" " .. FUEL_GLYPH .. " fuel ") or " fuel ", width)

  --- The muted row under a reading, at the most detail that fits.
  local function detail(text)
    return line({
      { text = "   " .. widgets.truncate(text, math.max(1, width - 3)), style = { fg = theme.muted } },
    })
  end

  --- A note pushed to the right edge, or dropped when it would not fit.
  local function flush_right(row, note, style)
    if not note then
      return
    end
    local pad = width - row.used - widgets.len(note)
    if pad >= 2 then
      row:add(string.rep(" ", pad))
      row:add(note, style)
    end
  end

  if not fuel or #fuel == 0 then
    local row = ui.row({ width = width })
    row:add(lead, { fg = theme.muted })
    row:add(widgets.truncate_hard(spinner .. " reading", math.max(0, width - row.used)),
      { fg = theme.muted })
    return { line(row:spans_list()) }
  end

  local shown = {}
  for _, rec in ipairs(fuel) do
    if rec.remaining and not rec.unavailable then
      shown[#shown + 1] = rec
    end
  end

  -- THE AGE BELONGS TO THE BLOCK, not to a provider: it is one probe, and
  -- every record in it was read at the same instant. The reserve is the
  -- block's too — one floor, applied to every provider — which is what frees
  -- each reading's row for its bar.
  local age = read_age(fuel[1])
  local head = ui.row({ width = width })
  head:add(lead, { fg = theme.muted })

  if #shown == 0 then
    head:add(widgets.truncate_hard("unavailable", math.max(0, width - head.used)),
      { fg = theme.warn })
    flush_right(head, age, { fg = theme.muted })
    -- The first record's reason, named: fleet's own provider leads the record,
    -- so this is the one whose failure matters most to what runs below.
    local first = fuel[1]
    local why = first.unavailable or "no reading"
    if (first.provider or "") ~= "" then
      why = first.provider .. " — " .. why
    end
    return { line(head:spans_list()), detail(why) }
  end

  local reserve = fuel[1].reserve and ("reserve " .. fuel[1].reserve .. "%") or ""
  local right = age and (widgets.len(age) + 2) or 0
  if reserve ~= "" and width - head.used - right >= widgets.len(reserve) then
    head:add(reserve, { fg = theme.muted })
  end
  flush_right(head, age, { fg = theme.muted })

  local rows = { line(head:spans_list()) }

  -- The widest name drawn, held to what the column can spend on names: the
  -- LABEL gives way before the number does, because a truncated provider is
  -- still the right provider and a truncated percentage is not a reading.
  local label_width = 1
  for _, rec in ipairs(shown) do
    label_width = math.max(label_width, widgets.len(rec.provider or ""))
  end
  label_width = math.min(label_width, FUEL_LABEL_MAX, math.max(1, width - 2 - FUEL_NUMBER))

  for _, rec in ipairs(shown) do
    local row = ui.row({ width = width })
    row:add(" ")
    row:add(widgets.pad(widgets.truncate(rec.provider or "", label_width), label_width),
      { fg = theme.muted })
    row:add(" ")
    local number = rec.remaining .. "%"
    number = string.rep(" ", math.max(0, FUEL_NUMBER - widgets.len(number))) .. number
    -- quota-axi's own word for its reading, passed through rather than
    -- interpreted: it means the number is remembered, not just observed.
    local note = rec.stale and "stale" or nil
    local room = width - row.used
    -- Dropped rather than overflowed. The hatched bar says the same thing, and
    -- where there is no room for a bar either, `fleet-status.sh` still does.
    if note and widgets.len(number) + widgets.len(note) + 1 > room then
      note = nil
    end
    local cells = room - widgets.len(number) - 1
      - (note and (widgets.len(note) + 1) or 0)
    if cells >= FUEL_BAR_MIN then
      for _, span in ipairs(bar_spans(rec, cells)) do
        row:add(span.text, span.style)
      end
      row:add(" ")
    end
    row:add(number, { fg = fuel_tone(rec), bold = true })
    if note then
      row:add(" " .. note, { fg = theme.warn })
    end
    rows[#rows + 1] = line(row:spans_list())
  end

  if #shown == 1 then
    -- The widest level that fits, and the narrowest one when none does — which
    -- `detail` then truncates, the same last resort the documents row takes.
    local budget = math.max(1, width - 3)
    local chosen
    for _, level in ipairs(FUEL_DETAIL) do
      local segs = detail_segments(shown[1], level)
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
        fuel = { { unavailable = "the fuel probe did not run" } }
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
