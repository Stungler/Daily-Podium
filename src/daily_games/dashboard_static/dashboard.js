"use strict";

const $ = (id) => document.getElementById(id);
const state = { game: "all", day: "today", period: "day", server: "", data: null, busy: false, changed: false, lastSuccess: null, stale: false };
const names = { timeguessr: "TimeGuessr", sizeitup: "Size It Up", metazooa: "Metazooa" };
const games = Object.keys(names);
const fmt = new Intl.NumberFormat();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function text(id, value) { $(id).textContent = value; }
function niceDay(value) {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(value + "T12:00:00Z"));
}
function clock(value, zone) {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: zone }).format(new Date(value));
}
function age(seconds) {
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`;
}
function gameScore(game, result) {
  if (!result) return "—";
  if (!result.solved) return "Failed";
  return game === "metazooa" ? `${fmt.format(result.score)} guesses` : fmt.format(result.score);
}
function cell(tag, textValue, className) { return el(tag, className, textValue); }

function renderBoard(selected) {
  const board = selected.board;
  const activeGames = state.game === "all" ? games : [state.game];
  const periodBoard = selected.period_board;
  const label = { day: "Daily", week: "Weekly", month: "Monthly" }[state.period];
  text("board-title", `${state.game === "all" ? "Combined" : names[state.game]} · ${label.toLowerCase()} standings`);
  text("board-caption", periodBoard ? `${periodBoard.start} to ${periodBoard.through} — ${names[state.game] || "Combined standings"}` : `${niceDay(board.day)} — ${names[state.game] || "Combined standings"}`);
  const status = periodBoard ? `${periodBoard.finalized_days}/${periodBoard.expected_days} days final` : { final: "Final", closed_pending: "Closed · final pending", provisional: "Provisional" }[selected.board_state];
  text("board-state", status);
  const periodComplete = periodBoard && periodBoard.expected_days > 0 && periodBoard.finalized_days === periodBoard.expected_days;
  $("board-state").className = `badge ${periodBoard ? periodComplete ? "final" : "pending" : selected.board_state === "final" ? "final" : selected.board_state === "closed_pending" ? "pending" : ""}`;
  document.querySelectorAll("[data-game]").forEach((button) => {
    const active = button.dataset.game === state.game;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (periodBoard) {
    const head = el("tr");
    ["Rank", "Player", "Points", "Days", "Wins"].forEach((name) => head.append(cell("th", name)));
    $("board-head").replaceChildren(head);
    $("board-rows").replaceChildren(...periodBoard.rows.map((row, index) => {
      const tr = el("tr");
      const rank = cell("td"); rank.append(el("span", `rank${row.rank === 1 ? " top" : ""}`, row.rank)); tr.append(rank);
      const playerCell = cell("td"); const player = el("div", "player");
      const initials = row.name.trim().split(/\s+/u).slice(0, 2).map((word) => [...word][0]).join("").toUpperCase() || "?";
      player.append(el("span", `avatar tone-${index % 4}`, initials), el("span", "player-name", row.name));
      player.title = row.name; playerCell.append(player); tr.append(playerCell);
      const points = cell("td", undefined, "total"); points.append(document.createTextNode(fmt.format(row.points)), el("span", "", "pts"));
      tr.append(points, cell("td", row.days, "score"), cell("td", row.wins, "score"));
      return tr;
    }));
    $("board-empty").hidden = periodBoard.rows.length > 0;
    text("scoring", "Saved daily points added · ties share rank · wins include ties");
    text("board-note", `${periodBoard.start} → ${periodBoard.through} · ${periodBoard.finalized_days}/${periodBoard.expected_days} days finalized`);
    return;
  }
  const head = el("tr");
  ["Rank", "Player", ...activeGames.map((game) => names[game]), "Points"].forEach((name) => head.append(cell("th", name)));
  $("board-head").replaceChildren(head);
  const rows = board.rows.map((row, index) => {
    const tr = el("tr");
    const rank = cell("td");
    rank.append(el("span", `rank${row.rank === 1 ? " top" : ""}`, row.rank));
    tr.append(rank);
    const playerCell = cell("td");
    const player = el("div", "player");
    const initials = row.name.trim().split(/\s+/u).map((word) => [...word][0]).slice(0, 2).join("").toUpperCase() || "?";
    player.append(el("span", `avatar tone-${index % 4}`, initials), el("span", "player-name", row.name));
    player.title = row.name;
    playerCell.append(player);
    tr.append(playerCell);
    activeGames.forEach((game) => {
      const result = row.games[game];
      const score = cell("td", undefined, "score");
      score.append(el("span", result ? "" : "muted", gameScore(game, result)));
      if (result) score.append(el("span", "points", `+${result.points}`));
      tr.append(score);
    });
    const total = cell("td", undefined, "total");
    total.append(document.createTextNode(fmt.format(row.total)), el("span", "", "pts"));
    tr.append(total);
    return tr;
  });
  $("board-rows").replaceChildren(...rows);
  $("board-empty").hidden = rows.length > 0;
  text("scoring", board.scoring === "placement" ? "Placement scoring · tied ranks shared" : "1 + players beaten · tied ranks shared");
  let note = `${niceDay(board.day)} · ${board.timezone}`;
  if (selected.posting) note += selected.posting.posted ? " · Posted to Discord" : " · Posting pending";
  text("board-note", note);
}

function renderActivity(selected) {
  const rows = selected.recent.map((item) => {
    const row = el("div", "activity-row");
    const detail = el("div");
    detail.append(el("div", "activity-name", item.name), el("div", "activity-detail", `${names[item.game]} · ${item.day} · puzzle ${item.puzzle}`));
    const value = el("div", "activity-value", gameScore(item.game, item));
    const time = el("time", "", clock(item.submitted_at, selected.config.timezone));
    time.dateTime = item.submitted_at;
    value.append(time);
    row.append(detail, value);
    return row;
  });
  $("activity").replaceChildren(...(rows.length ? rows : [el("p", "muted", "No accepted results yet.")]));
  const history = selected.history.map((item) => {
    const row = el("div", "history-row");
    const button = el("button", "history-button", niceDay(item.day));
    button.type = "button";
    button.addEventListener("click", () => {
      state.day = item.day;
      $("day").value = "custom";
      $("custom-day").hidden = false;
      $("custom-day").value = item.day;
      refresh();
      $("board-title").scrollIntoView({ behavior: "smooth", block: "center" });
    });
    row.append(button, el("span", `badge ${item.posted ? "final" : "pending"}`, item.posted ? "Posted" : "Pending"));
    return row;
  });
  $("history").replaceChildren(...(history.length ? history : [el("p", "muted", "Final boards appear here after the first cutoff.")]));
}

function render(data) {
  const selected = data.selected;
  const options = data.servers.map((server) => {
    const option = el("option", "", `Server ${server.id}`);
    option.value = server.id;
    return option;
  });
  $("server").replaceChildren(...(options.length ? options : [el("option", "", "No configured servers")]));
  $("server").disabled = !selected;
  $("content").hidden = !selected;
  $("empty").hidden = Boolean(selected);
  if (!selected) return;
  state.server = selected.server_id;
  $("server").value = state.server;
  text("players", fmt.format(selected.today_counts.players));
  text("submissions", fmt.format(selected.today_counts.submissions));
  text("competition-day", niceDay(selected.today));
  text("timezone", selected.config.timezone);
  text("cutoff-label", `${selected.config.cutoff} · ${selected.config.timezone}`);
  const bot = data.bot;
  text("process-state", bot.state === "running" ? `Running · ${bot.processes.map((item) => item.pid).join(", ")}` : bot.state === "unknown" ? "Unknown" : "Not detected");
  $("process-state").className = bot.state === "running" ? "good" : "warn";
  $("health-dot").className = `health-light ${bot.state === "running" ? "running" : ""}`;
  text("uptime", bot.processes.length ? age(bot.processes[0].uptime_seconds) : "—");
  text("database-state", "Readable");
  $("database-state").className = "good";
  text("latest-result", selected.recent.length ? clock(selected.recent[0].submitted_at, selected.config.timezone) : "No results yet");
  $("participation").replaceChildren(...games.map((game) => {
    const row = el("div", "participation-item");
    const label = el("div", "participation-label");
    const count = selected.today_counts.games[game];
    label.append(el("span", "", names[game]), el("span", "", `${count} / ${selected.today_counts.players} players`));
    const bar = el("progress");
    bar.max = Math.max(1, selected.today_counts.players);
    bar.value = count;
    bar.setAttribute("aria-label", `${names[game]} participation`);
    row.append(label, bar);
    return row;
  }));
  text("setting-timezone", selected.config.timezone);
  text("setting-cutoff", selected.config.cutoff);
  text("setting-post", clock(selected.post_due_at, selected.config.timezone));
  text("setting-scoring", selected.config.scoring === "placement" ? "Placement" : "1 + players beaten");
  text("setting-input", selected.config.input_channel_id);
  text("setting-output", selected.config.output_channel_id);
  renderBoard(selected);
  renderActivity(selected);
  tick();
}

async function refresh() {
  if (state.busy) { state.changed = true; return; }
  state.busy = true;
  state.changed = false;
  $("refresh").disabled = true;
  const requested = { server: state.server, day: state.day, game: state.game, period: state.period };
  try {
    const query = new URLSearchParams(requested);
    const response = await fetch(`/api/data?${query}`, { cache: "no-store", signal: AbortSignal.timeout(10000) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Dashboard could not load data.");
    // A later control change should never display an earlier request under new tabs.
    if (state.changed) return;
    state.data = data;
    state.lastSuccess = new Date(data.generated_at);
    state.stale = false;
    $("notice").hidden = true;
    render(data);
  } catch (error) {
    state.stale = true;
    $("notice").hidden = false;
    $("notice").textContent = `${error.name === "TimeoutError" ? "Dashboard request timed out." : error.message} ${state.data ? "Showing the last successful view; its status and results may be stale." : "Check that the dashboard can read the bot database."}`;
    text("freshness", "Refresh failed · retrying automatically");
    text("countdown", "—");
    text("process-state", "Unknown · refresh failed");
    text("database-state", "Not checked · refresh failed");
    $("process-state").className = "warn";
    $("database-state").className = "warn";
    $("health-dot").className = "health-light";
  } finally {
    state.busy = false;
    $("refresh").disabled = false;
    if (state.changed) refresh();
  }
}

function tick() {
  if (state.lastSuccess && !state.stale) text("freshness", `Updated ${age((Date.now() - state.lastSuccess.getTime()) / 1000)} ago · auto-refresh on`);
  if (!state.data?.selected || state.stale) return;
  const remaining = Math.max(0, Math.floor((new Date(state.data.selected.closes_at).getTime() - Date.now()) / 1000));
  const h = Math.floor(remaining / 3600).toString().padStart(2, "0");
  const m = Math.floor(remaining % 3600 / 60).toString().padStart(2, "0");
  const s = (remaining % 60).toString().padStart(2, "0");
  text("countdown", remaining ? `${h}:${m}:${s}` : "Cutoff reached");
}

$("server").addEventListener("change", () => { state.server = $("server").value; refresh(); });
document.querySelectorAll("[data-game]").forEach((button) => button.addEventListener("click", () => { state.game = button.dataset.game; refresh(); }));
$("period").addEventListener("change", () => { state.period = $("period").value; refresh(); });
$("day").addEventListener("change", () => {
  $("custom-day").hidden = $("day").value !== "custom";
  if ($("day").value === "custom") {
    if (!$("custom-day").value) $("custom-day").value = state.data?.selected?.today || new Date().toISOString().slice(0, 10);
    state.day = $("custom-day").value;
  } else state.day = $("day").value;
  refresh();
});
$("custom-day").addEventListener("change", () => { if ($("custom-day").value && $("custom-day").validity.valid) { state.day = $("custom-day").value; refresh(); } });
$("refresh").addEventListener("click", refresh);
setInterval(refresh, 20000);
setInterval(tick, 1000);
refresh();
