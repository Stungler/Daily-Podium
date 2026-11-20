"use strict";
const $ = (id) => document.getElementById(id);
let names = {};
const params = new URLSearchParams(location.search);
const initialSlug = location.pathname.startsWith("/s/") ? location.pathname.slice(3) : "global";
const state = {scope: initialSlug, lastServer: initialSlug === "global" ? "" : initialSlug, day: params.get("day") || "today", game: params.get("game") || "all", period: params.get("period") || "day", busy: false, changed: false, data: null, stale: false};
const fmt = new Intl.NumberFormat();
function node(tag, cls, value) { const n = document.createElement(tag); if (cls) n.className = cls; if (value !== undefined) n.textContent = String(value); return n; }
function streakChip(streak) { if (!streak?.best) return null; const chip = node("span", "streak-chip", `🔥 ${streak.current}/${streak.best}`); chip.title = `${streak.current} current days · ${streak.best} best days`; chip.setAttribute("aria-label", chip.title); return chip; }
function text(id, value) { $(id).textContent = value; }
function dateLabel(day) { return new Intl.DateTimeFormat(undefined, {day: "numeric", month: "short", year: "numeric", timeZone: "UTC"}).format(new Date(`${day}T12:00:00Z`)); }
function chooseDay(day) { state.day = day; $("day").value = ["today", "yesterday"].includes(day) ? day : "custom"; $("custom-day").hidden = $("day").value !== "custom"; if (!$("custom-day").hidden) $("custom-day").value = day; }
function navigate(scope) { state.scope = scope; if (scope !== "global") state.lastServer = scope; load(); }

function updateGameTabs(lineup) {
  const options = ["all", ...lineup];
  if (!options.includes(state.game)) options.push(state.game);

  const tabs = $("game-tabs");
  if ([...tabs.children].map((button) => button.dataset.game).join(",") !== options.join(",")) {
    tabs.replaceChildren(...options.map((game) => {
      const button = node("button", "tab", game === "all" ? "Combined" : names[game]);
      button.type = "button";
      button.dataset.game = game;
      return button;
    }));
  }
  for (const button of tabs.children) {
    const active = button.dataset.game === state.game;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function wikiScore(result) {
  if (result.elapsed_seconds == null) return `${result.score} steps`;
  const minutes = Math.floor(result.elapsed_seconds / 60);
  const seconds = String(result.elapsed_seconds % 60).padStart(2, "0");
  return `${result.score} steps · ${minutes}:${seconds}`;
}

function render(data) {
  names = Object.fromEntries(data.games.map(({id, name}) => [id, name]));
  const board = data.board;
  const global = data.scope === "global";
  text("title", global ? "The global podium." : `${data.name}.`);
  text("subtitle", global ? "One field across every community. Your first accepted result counts." : "Your community. Your competition. Your daily podium.");
  document.title = `${data.name} · Daily Podium`;
  $("community").replaceChildren(...data.servers.map((server) => { const option = node("option", "", server.name); option.value = server.slug; return option; }));
  if (!state.lastServer && data.servers.length) state.lastServer = data.servers[0].slug;
  $("community").value = global ? state.lastServer : data.scope;
  $("community").disabled = !data.servers.length;
  $("server-mode").disabled = !state.lastServer;
  for (const [id, active] of [["server-mode", !global], ["global-mode", global]]) { $(id).classList.toggle("active", active); $(id).setAttribute("aria-pressed", String(active)); }
  text("players", fmt.format(data.players)); text("results", fmt.format(data.submissions)); text("board-date", dateLabel(board.day)); text("board-timezone", board.timezone); text("cutoff", `${global ? "Global: " : ""}${board.cutoff} · ${board.timezone}`);
  const periodBoard = data.period_board;
  text("board-title", `${state.game === "all" ? "Combined" : names[state.game]} · ${{day: "daily", week: "weekly", month: "monthly"}[state.period]} standings`);
  text("caption", periodBoard ? `${data.name} · ${periodBoard.start} to ${periodBoard.through}` : `${data.name} · ${board.day}`);
  text("board-state", periodBoard ? `${periodBoard.finalized_days}/${periodBoard.expected_days} days final` : {final: "Final", closed_pending: "Closed · final pending", provisional: "Provisional"}[data.state]);
  const periodComplete = periodBoard && periodBoard.expected_days > 0 && periodBoard.finalized_days === periodBoard.expected_days;
  $("board-state").className = `badge ${periodBoard ? periodComplete ? "final" : "pending" : data.state === "final" ? "final" : data.state === "closed_pending" ? "pending" : ""}`;
  if (periodBoard) {
    const head = node("tr"); ["Rank", "Player", "Points", "Days", "Wins"].forEach((label) => head.append(node("th", "", label))); $("head").replaceChildren(head);
    $("rows").replaceChildren(...periodBoard.rows.map((entry, index) => {
      const row = node("tr"); const rank = node("td"); rank.append(node("span", `rank${entry.rank === 1 ? " top" : ""}`, entry.rank)); row.append(rank);
      const playerCell = node("td"); const player = node("div", "player"); const initials = entry.name.trim().split(/\s+/u).slice(0, 2).map((part) => [...part][0]).join("").toUpperCase() || "?";
      player.append(node("span", `avatar tone-${index % 4}`, initials), node("span", "player-name", entry.name)); const streak = streakChip(entry.streak); if (streak) player.append(streak); player.title = entry.name; playerCell.append(player); row.append(playerCell);
      const points = node("td", "total", fmt.format(entry.points)); points.append(node("span", "", "pts")); row.append(points, node("td", "score", entry.days), node("td", "score", entry.wins)); return row;
    }));
    $("empty").hidden = periodBoard.rows.length > 0;
    text("scoring", "Saved daily points added · ties share rank · 🔥 current/best");
    text("final-note", `${periodBoard.start} → ${periodBoard.through} · ${periodBoard.finalized_days}/${periodBoard.expected_days} days finalized`);
  } else {
  const games = state.game === "all" ? board.lineup : [state.game];
  const head = node("tr"); ["Rank", "Player", ...games.map((game) => names[game]), "Points"].forEach((label) => head.append(node("th", "", label))); $("head").replaceChildren(head);
  $("rows").replaceChildren(...board.rows.map((entry, index) => {
    const row = node("tr"); const rank = node("td"); rank.append(node("span", `rank${entry.rank === 1 ? " top" : ""}`, entry.rank)); row.append(rank);
    const playerCell = node("td"); const player = node("div", "player"); const initials = entry.name.trim().split(/\s+/u).slice(0, 2).map((part) => [...part][0]).join("").toUpperCase() || "?";
    player.append(node("span", `avatar tone-${index % 4}`, initials), node("span", "player-name", entry.name)); const streak = streakChip(entry.streak); if (streak) player.append(streak); player.title = entry.name; playerCell.append(player); row.append(playerCell);
    games.forEach((game) => { const result = entry.games[game]; const score = node("td", "score"); const label = !result ? "—" : !result.solved ? "Failed" : game === "wikigame" ? wikiScore(result) : game === "metazooa" ? `${fmt.format(result.score)} guesses` : fmt.format(result.score); score.append(node("span", result ? "" : "muted", label)); if (result) score.append(node("span", "points", `+${result.points}`)); row.append(score); });
    const total = node("td", "total", fmt.format(entry.total)); total.append(node("span", "", "pts")); row.append(total); return row;
  }));
  $("empty").hidden = board.rows.length > 0;
  text("scoring", `${board.scoring === "placement" ? "Placement scoring" : "1 + players beaten"} · ties share rank · 🔥 current/best`);
  text("final-note", data.state === "final" ? (global ? "All participating server boards finalized" : "Saved final standings") : "Standings can change until finalized");
  }
  text("rules-title", global ? "One player. One result per game." : "This community's competition"); text("rules", data.rules);
  $("history").replaceChildren(...(data.history.length ? data.history.map((day) => { const row = node("div", "history-row"); const button = node("button", "history-button", dateLabel(day)); button.type = "button"; button.addEventListener("click", () => { chooseDay(day); load(); }); row.append(button); return row; }) : [node("p", "muted", "No board history yet.")]));
  updateGameTabs(data.period_games);
  const query = new URLSearchParams({day: state.day, game: state.game, period: state.period});
  history.replaceState(null, "", `${global ? "/global" : `/s/${data.scope}`}?${query}`);
  tick();
}
async function load() {
  if (state.busy) { state.changed = true; return; }
  state.busy = true; state.changed = false; $("refresh").disabled = true;
  try {
    const query = new URLSearchParams({scope: state.scope, day: state.day, game: state.game, period: state.period});
    const response = await fetch(`/api/leaderboard?${query}`, {cache: "no-store", signal: AbortSignal.timeout(10000)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || "Could not load standings.");
    if (state.changed) return;
    state.data = data; state.stale = false; $("notice").hidden = true; render(data);
  } catch (error) { state.stale = true; $("notice").hidden = false; text("notice", `${error.name === "TimeoutError" ? "Request timed out." : error.message}${state.data ? " Showing the last successful view; standings may be stale." : ""}`); text("freshness", "Refresh failed · retrying automatically"); text("countdown", "—"); }
  finally { state.busy = false; $("refresh").disabled = false; if (state.changed) load(); }
}
function tick() {
  if (!state.data || state.stale) return;
  const age = Math.max(0, Math.floor((Date.now() - new Date(state.data.generated_at).getTime()) / 1000)); text("freshness", `Updated ${age}s ago · auto-refresh on`);
  const remaining = Math.max(0, Math.floor((new Date(state.data.closes_at).getTime() - Date.now()) / 1000));
  text("countdown", remaining ? [Math.floor(remaining / 3600), Math.floor(remaining % 3600 / 60), remaining % 60].map((part) => String(part).padStart(2, "0")).join(":") : "Cutoff reached");
}
chooseDay(state.day);
$("period").value = state.period;
$("community").addEventListener("change", () => navigate($("community").value));
$("server-mode").addEventListener("click", () => { if (state.lastServer) navigate(state.lastServer); });
$("global-mode").addEventListener("click", () => navigate("global"));
$("game-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-game]");
  if (button) {
    state.game = button.dataset.game;
    load();
  }
});
$("period").addEventListener("change", () => { state.period = $("period").value; load(); });
$("day").addEventListener("change", () => { chooseDay($("day").value === "custom" ? $("custom-day").value || state.data?.today || new Date().toISOString().slice(0, 10) : $("day").value); load(); });
$("custom-day").addEventListener("change", () => { if ($("custom-day").value && $("custom-day").validity.valid) { state.day = $("custom-day").value; load(); } });
$("refresh").addEventListener("click", load);
setInterval(load, 20000); setInterval(tick, 1000); load();
