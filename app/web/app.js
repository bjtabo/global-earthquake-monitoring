"use strict";

const STALE_AFTER_SECONDS = 900;

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  return response.json();
}

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
  return td;
}

function quakeRow(quake) {
  const row = document.createElement("tr");
  cell(row, quake.event_time.replace("T", " ").slice(0, 16));
  cell(row, quake.mag === null ? "n/a" : quake.mag.toFixed(1));
  const place = cell(row, "");
  const label = quake.place || quake.id;
  if (quake.url) {
    const link = document.createElement("a");
    link.href = quake.url;
    link.rel = "noopener";
    link.textContent = label;
    place.appendChild(link);
  } else {
    place.textContent = label;
  }
  cell(row, quake.depth_km === null ? "n/a" : quake.depth_km.toFixed(1));
  return row;
}

function fill(tableId, stateId, rows, emptyText) {
  const table = document.getElementById(tableId);
  const state = document.getElementById(stateId);
  table.querySelector("tbody").replaceChildren(...rows);
  table.hidden = rows.length === 0;
  state.hidden = rows.length > 0;
  state.textContent = emptyText;
}

function fail(stateId, error) {
  const state = document.getElementById(stateId);
  state.hidden = false;
  state.textContent = `Could not load this section: ${error.message}`;
}

function showFreshness(seconds) {
  const status = document.getElementById("freshness");
  if (seconds === null || seconds === undefined) {
    status.textContent = "No USGS data has been loaded yet.";
    return;
  }
  const minutes = Math.round(seconds / 60);
  status.textContent = `Data from USGS, updated ${minutes} minute${minutes === 1 ? "" : "s"} ago.`;
  const stale = seconds > STALE_AFTER_SECONDS;
  status.classList.toggle("stale", stale);
  if (stale) status.textContent += " Updates are delayed; this is the latest data available.";
}

async function loadSignificant() {
  try {
    const data = await getJson("/api/quakes/significant");
    showFreshness(data.data_age_seconds);
    fill("significant", "significant-state", data.earthquakes.map(quakeRow),
      "No earthquakes above magnitude 4.0 in the last 24 hours.");
  } catch (error) {
    document.getElementById("freshness").textContent = "Could not check how recent the data is.";
    fail("significant-state", error);
  }
}

async function loadDaily() {
  try {
    const data = await getJson("/api/stats/daily-average");
    const average = document.getElementById("daily-average");
    average.hidden = false;
    average.textContent = `Average: ${data.average} earthquakes per day`;
    const rows = data.days.map((day) => {
      const row = document.createElement("tr");
      cell(row, day.date);
      cell(row, String(day.count));
      return row;
    });
    fill("daily", "daily-state", rows, "No data for the last 7 days.");
  } catch (error) {
    fail("daily-state", error);
  }
}

function dayAfter(value) {
  const day = new Date(`${value}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}

async function search(event) {
  if (event) event.preventDefault();
  const form = new FormData(document.getElementById("search-form"));
  const params = new URLSearchParams({ limit: form.get("limit") || "5" });
  if (form.get("start")) params.set("start", form.get("start"));
  if (form.get("end")) params.set("end", dayAfter(form.get("end")));  // the "To" day is inclusive
  if (form.get("min_mag")) params.set("min_mag", form.get("min_mag"));
  const state = document.getElementById("search-state");
  state.hidden = false;
  state.textContent = "Searching…";
  try {
    const data = await getJson(`/api/quakes/search?${params}`);
    fill("search", "search-state", data.earthquakes.map(quakeRow), "No earthquakes match this search.");
  } catch (error) {
    fail("search-state", error);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("search-form").addEventListener("submit", search);
  loadSignificant();
  loadDaily();
  search();
});
