-- Synthetic, public-safe records for native thurbox screen captures.
local board = require("lib.fleet_board")
local model = { health = "ticking", read_at = 2000000000, topics = {} }
local specs = {
  {
    "atlas",
    "Audit exports",
    {
      { "01", "queued", "Add audit export", "codex", "", "", "" },
      { "02", "dispatched", "Export worker", "codex", "", "", "" },
      {
        "03",
        "done",
        "Validate every exported record",
        "codex",
        "pr",
        "checks-failed",
        "https://github.com/example/project/pull/204",
      },
      { "04", "stuck", "Recover test database", "codex", "", "", "" },
      { "00", "landed", "Audit schema", "codex", "pr", "merged", "https://github.com/example/project/pull/201" },
    },
  },
  {
    "beacon",
    "Search index",
    {
      { "03", "queued", "Wire search index", "claude", "", "", "" },
      { "02", "dispatched", "Search service", "claude", "", "", "" },
      {
        "01",
        "done",
        "Schema migration",
        "claude",
        "pr",
        "checks-passed",
        "https://gitlab.example.org/group/project/-/merge_requests/81",
      },
      { "04", "done", "Search architecture", "claude", "served", "served", "" },
    },
  },
  {
    "cedar",
    "Platform adapters",
    {
      { "02", "queued", "Stage release", "codex", "", "", "" },
      { "01", "dispatched", "Update adapters", "codex", "", "", "" },
      { "03", "done", "Document adapters", "codex", "pr", "ready", "https://github.com/example/project/pull/206" },
      { "04", "done", "Platform walkthrough", "codex", "served", "served", "" },
      { "05", "abandoned", "Legacy cleanup", "claude", "", "", "" },
    },
  },
}
for _, spec in ipairs(specs) do
  local topic = { slug = spec[1], title = spec[2], tasks = {} }
  model.topics[#model.topics + 1] = topic
  for _, row in ipairs(spec[3]) do
    local blocked = {}
    if spec[1] == "beacon" and row[1] == "03" then
      blocked = { { ref = "beacon/02", kind = "consumes", cleared = false } }
    end
    if spec[1] == "cedar" and row[1] == "02" then
      blocked = { { ref = "release sign-off", kind = "awaiting-approval", condition = true, cleared = false } }
    end
    topic.tasks[#topic.tasks + 1] = {
      id = row[1],
      state = row[2],
      display_state = #blocked > 0 and "waiting" or row[2],
      title = row[3],
      agent = row[4],
      host = spec[1] == "beacon" and "remote" or "",
      session = "",
      publish_method = row[5],
      publish_state = row[6],
      artifact = row[7],
      review = row[2] == "done" and "https://example.org/reviews/" .. spec[1] or "",
      publish_detail = row[6] == "checks-failed" and "failed checks: unit" or "",
      threads = row[6] == "checks-failed" and 2 or nil,
      blocked_by = blocked,
      moved_at = 2000000000 - 2400,
    }
  end
end
local fuel = {
  {
    provider = "Codex",
    remaining = 61,
    reserve = 20,
    windows = {
      { label = "5h", remaining = 82 },
      { label = "7d", remaining = 61 },
    },
  },
  {
    provider = "Claude",
    remaining = 54,
    reserve = 20,
    windows = {
      { label = "5h", remaining = 54 },
      { label = "7d", remaining = 73 },
    },
  },
}
local keys = { { key = "alt+k", action = "fleetqueue.board", scope = "global", desc = "toggle board" } }
for key, name in pairs({
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
  h = "scope",
}) do
  keys[#keys + 1] = { key = key, action = "fleetqueue.board_" .. name, desc = name }
end
return {
  name = "fleet-board-capture",
  slot = "float",
  floats = true,
  pure = false,
  keys = keys,
  commands = { { action = "fleetqueue.board_topic_all", desc = "all topics" } },
  render = function(ctx)
    if not state.board_open then
      return { type = "text", text = "" }
    end
    model.read_at = (thurbox.taken_at_ms or 2000000000000) / 1000
    for _, topic in ipairs(model.topics) do
      for _, task in ipairs(topic.tasks) do
        task.moved_at = model.read_at - 2400
      end
    end
    return board.render(ctx, model, fuel, { id = "fixture" }, "")
  end,
  on_action = function(action)
    if action == "fleetqueue.board" then
      state.board_open = not state.board_open
      state.board_picker = nil
      return true
    end
    if state.board_open then
      return board.on_action(action)
    end
    return false
  end,
  on_key = function(key)
    return state.board_open and board.on_key(key) or false
  end,
  on_click = board.on_click,
  on_scroll = board.on_scroll,
}
