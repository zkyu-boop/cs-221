"use strict";

const byId = id => document.getElementById(id);
const roomCode = new URLSearchParams(location.search).get("trip");
const storageKey = code => "weekend-together:" + code;
const joinForm = byId("join");
const createForm = byId("create");
const preferencesForm = byId("preferences");
const feedbackForm = byId("feedback");
const activitySelect = byId("activity");
let session = null;
let room = null;
let catalog = [];
let busy = false;

function message(text, error = false) {
  const status = byId("status");
  status.textContent = text;
  status.classList.toggle("error", error);
  status.hidden = !text;
}

async function request(path, method = "GET", body) {
  const headers = {};
  if (session) headers.Authorization = "Bearer " + session.token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {method, headers, body: JSON.stringify(body), cache: "no-store"});
  const result = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail) ? result.detail.map(item =>
      item.loc.slice(1).join(".") + ": " + item.msg).join("; ") : result.detail;
    const error = new Error(detail || "Request failed.");
    error.status = response.status;
    throw error;
  }
  return result;
}

function roomPath(suffix = "") {
  return "/trips/" + encodeURIComponent(session.trip_id) + suffix;
}

function inviteLink(code) {
  const url = new URL("/", location.origin);
  url.searchParams.set("trip", code);
  return url.href;
}

function openSession(member) {
  sessionStorage.setItem(storageKey(member.trip_id), JSON.stringify(member));
  location.assign(inviteLink(member.trip_id));
}

function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function checkbox(text, name, value, checked) {
  const label = element("label", "");
  const input = document.createElement("input");
  Object.assign(input, {type:"checkbox", name, value, checked});
  label.append(input, " " + text);
  return label;
}

function renderPreferences(saved) {
  if (saved) preferencesForm.elements.budget.value = saved.budget;
  byId("ratings").replaceChildren(...catalog.map(activity => {
    const label = element("label", activity.name);
    const select = document.createElement("select");
    select.name = "rating-" + activity.id;
    for (let i = 0; i <= 5; i++) select.add(new Option(i, i));
    select.value = saved?.ratings[activity.id] ?? (activity.id === "free" ? 1 : 3);
    label.append(select);
    return label;
  }));
  byId("unavailable").replaceChildren(...room.slots.map((label, index) =>
    checkbox(label, "unavailable", index, saved?.unavailable.includes(index) ?? false)));
  byId("excluded").replaceChildren(...catalog.filter(activity => activity.id !== "free").map(activity =>
    checkbox(activity.name, "excluded", activity.id, saved?.excluded.includes(activity.id) ?? false)));
}

function renderRoom(updated) {
  if (room && updated.revision < room.revision) return;
  if (room && updated.revision === room.revision) return;
  room = updated;
  const traveler = room.travelers.find(person => person.id === session.traveler_id);
  byId("title").textContent = room.title;
  byId("identity").textContent = traveler.name + (traveler.organizer ? " · Organizer" : " · Traveler");
  byId("invite").value = inviteLink(room.id);
  const ready = room.travelers.filter(person => person.ready).length;
  byId("readiness").textContent = ready + " of " + room.travelers.length + " travelers ready · " + room.capacity + " seats";
  byId("members").replaceChildren(...room.travelers.map(person =>
    element("li", person.name + (person.ready ? " · Ready" : " · Preferences pending"))));
  const canGenerate = ready >= 3 && ready === room.travelers.length;
  const generate = byId("generate");
  generate.hidden = !traveler.organizer || Boolean(room.plan && !room.stale);
  generate.disabled = !canGenerate;
  generate.textContent = room.plan ? "Update itinerary" : "Generate itinerary";
  byId("plan-message").textContent = room.stale ? "Preferences changed. The organizer can update this itinerary." :
    room.plan ? "" : canGenerate ? "Ready for the organizer to generate the itinerary." :
      "At least three travelers must join, and each joined traveler must save preferences.";
  const plan = room.plan;
  if (!plan) return;
  const percent = score => Math.round(score * 100);
  byId("metrics").textContent = "$" + plan.cost_per_traveler + " per traveler · " +
    percent(plan.average_satisfaction) + "% modeled satisfaction · " +
    percent(plan.satisfaction_gap) + " percentage point gap";
  byId("schedule").replaceChildren(...plan.slots.map(slot => {
    const item = element("li", "");
    item.classList.toggle("changed", slot.changed);
    item.append(element("small", slot.label + (slot.changed ? " · Revised" : "")),
      document.createElement("br"), element("strong", slot.name),
      document.createTextNode(" · $" + slot.cost));
    return item;
  }));
  const selected = activitySelect.value;
  const available = plan.slots.filter(slot => slot.id !== "free");
  activitySelect.replaceChildren(...available.map(slot => new Option(slot.label + " · " + slot.name, slot.index)));
  if (available.some(slot => String(slot.index) === selected)) activitySelect.value = selected;
  feedbackForm.hidden = room.stale || available.length === 0;
}

async function perform(action, control) {
  if (busy) return;
  busy = true;
  if (control) control.disabled = true;
  message("");
  try {
    await action();
  } catch (error) {
    message(error.message, true);
    if (session && error.status === 409) {
      try { renderRoom(await request(roomPath())); } catch { /* Keep the original error visible. */ }
    }
  } finally {
    busy = false;
    if (control === byId("generate")) {
      control.disabled = !room || room.travelers.length < 3 || room.travelers.some(person => !person.ready);
    } else if (control) {
      control.disabled = false;
    }
  }
}

createForm.addEventListener("submit", event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(createForm));
  for (const key of ["days", "capacity", "fairness", "stability"]) data[key] = Number(data[key]);
  perform(async () => openSession(await request("/trips", "POST", data)), event.submitter);
});

joinForm.addEventListener("submit", event => {
  event.preventDefault();
  const {code, name} = Object.fromEntries(new FormData(joinForm));
  perform(async () => openSession(await request("/trips/" + encodeURIComponent(code.trim()) + "/join", "POST", {name})), event.submitter);
});

preferencesForm.addEventListener("submit", event => {
  event.preventDefault();
  const data = new FormData(preferencesForm);
  const ratings = Object.fromEntries(catalog.map(activity => [activity.id, Number(data.get("rating-" + activity.id))]));
  perform(async () => {
    renderRoom(await request(roomPath("/preferences"), "PUT", {
      budget:Number(data.get("budget")), ratings,
      unavailable:data.getAll("unavailable").map(Number), excluded:data.getAll("excluded")
    }));
    message("Preferences saved.");
  }, event.submitter);
});

byId("generate").addEventListener("click", event => {
  perform(async () => renderRoom(await request(roomPath("/itinerary"), "POST", {revision:room.revision})), event.currentTarget);
});

feedbackForm.addEventListener("submit", event => {
  event.preventDefault();
  const slot = room.plan.slots[Number(activitySelect.value)];
  if (!slot) return;
  perform(async () => {
    renderRoom(await request(roomPath("/feedback"), "POST", {
      revision:room.revision, slot_index:slot.index, activity_id:slot.id
    }));
    message(slot.name + " removed. " + room.plan.changed_slots + " slots changed.");
  }, event.submitter);
});

byId("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(byId("invite").value);
    message("Invite link copied.");
  } catch {
    byId("invite").select();
    message("Copy the selected invite link.");
  }
});

async function start() {
  if (!roomCode) return;
  joinForm.elements.code.value = roomCode;
  try {
    session = JSON.parse(sessionStorage.getItem(storageKey(roomCode)));
    if (!session) return;
    const [activities, current, saved] = await Promise.all([
      request("/catalog"), request(roomPath()), request(roomPath("/preferences"))
    ]);
    catalog = activities;
    renderRoom(current);
    renderPreferences(saved);
    byId("lobby").hidden = true;
    byId("room").hidden = false;
    setInterval(async () => {
      if (busy || document.hidden) return;
      try {
        const updated = await request(roomPath());
        if (!busy) renderRoom(updated);
      } catch { /* The next refresh can recover from a transient disconnect. */ }
    }, 2500);
  } catch (error) {
    session = null;
    if (error.status === 401 || error.status === 404) sessionStorage.removeItem(storageKey(roomCode));
    message(error.message, true);
  }
}

start();
