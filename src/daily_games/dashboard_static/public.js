"use strict";
const $ = (id) => document.getElementById(id);
const names = {timeguessr: "TimeGuessr", sizeitup: "Size It Up", metazooa: "Metazooa"};
const params = new URLSearchParams(location.search);
const initialSlug = location.pathname.startsWith("/s/") ? location.pathname.slice(3) : "global";
const state = {scope: initialSlug, lastServer: initialSlug === "global" ? "" : initialSlug, day: params.get("day") || "today", game: params.get("game") || "all", busy: false, changed: false, data: null, stale: false};
const fmt = new Intl.NumberFormat();
function node(tag, cls, value) { const n = document.createElement(tag); if (cls) n.className = cls; if (value !== undefined) n.textContent = String(value); return n; }
function text(id, value) { $(id).textContent = value; }
function dateLabel(day) { return new Intl.DateTimeFormat(undefined, {day: "numeric", month: "short", year: "numeric", timeZone: "UTC"}).format(new Date(`${day}T12:00:00Z`)); }
function chooseDay(day) { state.day = day; $("day").value = ["today", "yesterday"].includes(day) ? day : "custom"; $("custom-day").hidden = $("day").value !== "custom"; if (!$("custom-day").hidden) $("custom-day").value = day; }
function navigate(scope) { state.scope = scope; if (scope !== "global") state.lastServer = scope; load(); }
function render(data) {
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
  text("board-title", `${state.game === "all" ? "Combined" : names[state.game]} standings`);
  text("caption", `${data.name} · ${board.day}`);
  text("board-state", {final: "Final", closed_pending: "Closed · final pending", provisional: "Provisional"}[data.state]);
  $("board-state").className = `badge ${data.state === "final" ? "final" : data.state === "closed_pending" ? "pending" : ""}`;
  const games = state.game === "all" ? Object.keys(names) : [state.game];
  const head = node("tr"); ["Rank", "Player", ...games.map((game) => names[game]), "Points"].forEach((label) => head.append(node("th", "", label))); $("head").replaceChildren(head);
  $("rows").replaceChildren(...board.rows.map((entry, index) => {
    const row = node("tr"); const rank = node("td"); rank.append(node("span", `rank${entry.rank === 1 ? " top" : ""}`, entry.rank)); row.append(rank);
    const playerCell = node("td"); const player = node("div", "player"); const initials = entry.name.trim().split(/\s+/u).slice(0, 2).map((part) => [...part][0]).join("").toUpperCase() || "?";
    player.append(node("span", `avatar tone-${index % 4}`, initials), node("span", "player-name", entry.name)); player.title = entry.name; playerCell.append(player); row.append(playerCell);
    games.forEach((game) => { const result = entry.games[game]; const score = node("td", "score"); const label = !result ? "—" : !result.solved ? "Failed" : game === "metazooa" ? `${fmt.format(result.score)} guesses` : fmt.format(result.score); score.append(node("span", result ? "" : "muted", label)); if (result) score.append(node("span", "points", `+${result.points}`)); row.append(score); });
    const total = node("td", "total", fmt.format(entry.total)); total.append(node("span", "", "pts")); row.append(total); return row;
  }));
  $("empty").hidden = board.rows.length > 0;
  text("scoring", board.scoring === "placement" ? "Placement scoring · ties share rank" : "1 + players beaten · ties share rank");
  text("final-note", data.state === "final" ? (global ? "All participating server boards finalized" : "Saved final standings") : "Standings can change until finalized");
  text("rules-title", global ? "One player. One result per game." : "This community's competition"); text("rules", data.rules);
  $("history").replaceChildren(...(data.history.length ? data.history.map((day) => { const row = node("div", "history-row"); const button = node("button", "history-button", dateLabel(day)); button.type = "button"; button.addEventListener("click", () => { chooseDay(day); load(); }); row.append(button); return row; }) : [node("p", "muted", "No board history yet.")]));
  document.querySelectorAll("[data-game]").forEach((button) => { const active = button.dataset.game === state.game; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active)); });
  const query = new URLSearchParams({day: state.day, game: state.game});
  history.replaceState(null, "", `${global ? "/global" : `/s/${data.scope}`}?${query}`);
  tick();
}
async function load() {
  if (state.busy) { state.changed = true; return; }
  state.busy = true; state.changed = false; $("refresh").disabled = true;
  try {
    const query = new URLSearchParams({scope: state.scope, day: state.day, game: state.game});
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
$("community").addEventListener("change", () => navigate($("community").value));
$("server-mode").addEventListener("click", () => { if (state.lastServer) navigate(state.lastServer); });
$("global-mode").addEventListener("click", () => navigate("global"));
document.querySelectorAll("[data-game]").forEach((button) => button.addEventListener("click", () => { state.game = button.dataset.game; load(); }));
$("day").addEventListener("change", () => { chooseDay($("day").value === "custom" ? $("custom-day").value || state.data?.today || new Date().toISOString().slice(0, 10) : $("day").value); load(); });
$("custom-day").addEventListener("change", () => { if ($("custom-day").value && $("custom-day").validity.valid) { state.day = $("custom-day").value; load(); } });
$("refresh").addEventListener("click", load);
setInterval(load, 20000); setInterval(tick, 1000); load();
