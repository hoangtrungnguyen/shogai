// Shōgai Relay page: ask someone with the key they gave you, and read the answers to what you asked.
// The key and your questions' tokens stay in this browser (localStorage). All text from the server is
// set with textContent, never as HTML.
"use strict";

const KEY_STORE = "shogai.relay.key";
const NAME_STORE = "shogai.relay.name";
const listStore = (keyId) => `shogai.relay.questions.${keyId}`;
const REFRESH_MS = 30_000;

const $ = (id) => document.getElementById(id);
const state = { key: null, keyId: null, to: null, timer: null, pendingTicket: null };

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function detail(data) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0] && d[0].msg) return d[0].msg;
  return "";
}

async function call(path, { method = "GET", body, token, key = state.key } = {}) {
  const headers = { Authorization: `Bearer ${key}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["X-Request-Token"] = token;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* not JSON */
  }
  if (!res.ok) throw new ApiError(res.status, detail(data) || `HTTP ${res.status}`);
  return data;
}

function store(name, value) {
  try {
    if (value === null) localStorage.removeItem(name);
    else localStorage.setItem(name, value);
  } catch {
    /* storage unavailable (private mode) */
  }
}

function load(name) {
  try {
    return localStorage.getItem(name);
  } catch {
    return null;
  }
}

function setText(el, text) {
  el.textContent = text || "";
  el.hidden = !text;
}

function ago(iso) {
  if (!iso) return "";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(s)) return "";
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d < 30 ? `${d} d ago` : new Date(iso).toLocaleDateString();
}

const toName = () => (state.to && state.to.name) || "this person";

/* ------------------------------------------------------------ saved questions */

function saved() {
  try {
    const list = JSON.parse(load(listStore(state.keyId)) || "[]");
    return Array.isArray(list) ? list.filter((s) => s && typeof s.id === "string" && typeof s.token === "string") : [];
  } catch {
    return [];
  }
}

function save(list) {
  store(listStore(state.keyId), JSON.stringify(list.slice(0, 50)));
}

function remember(id, token) {
  save([{ id, token }, ...saved().filter((s) => s.id !== id)]);
}

function forget(id) {
  save(saved().filter((s) => s.id !== id));
}

/* ------------------------------------------------------------ key */

async function openKey(key, { quiet = false } = {}) {
  setText($("key-error"), "");
  try {
    const info = await call("/api/key", { key });
    state.key = key;
    state.keyId = info.key_id;
    state.to = info.to || {};
    store(KEY_STORE, key);
    if (state.pendingTicket) {
      remember(state.pendingTicket.id, state.pendingTicket.token);
      state.pendingTicket = null;
    }
    showAsking();
    return true;
  } catch (e) {
    if (e.status === 401) store(KEY_STORE, null);
    if (!quiet) {
      setText($("key-error"), e.status === 401 ? "This key doesn’t work. Check it with the person who gave it to you." : e.message);
    }
    return false;
  }
}

function lostKey(text) {
  clearInterval(state.timer);
  store(KEY_STORE, null);
  state.key = state.keyId = state.to = null;
  $("key-input").value = "";
  $("mine-list").replaceChildren();
  $("asking").hidden = true;
  $("key-card").hidden = false;
  document.title = "Shōgai Relay";
  setText($("key-error"), text);
  $("key-input").focus();
}

function showAsking() {
  $("key-card").hidden = true;
  $("asking").hidden = false;
  $("to-name").textContent = (state.to && state.to.name) || "The person who gave you this key";
  setText($("to-headline"), state.to && state.to.headline);
  document.querySelectorAll("[data-to]").forEach((el) => {
    el.textContent = toName();
  });
  document.title = state.to && state.to.name ? `Ask ${state.to.name} · Shōgai Relay` : "Shōgai Relay";
  $("ask-name").value = load(NAME_STORE) || "";
  void refresh();
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    if (!document.hidden) void refresh();
  }, REFRESH_MS);
}

/* ------------------------------------------------------------ questions */

function privateLink(s) {
  return `${location.origin}${location.pathname}#request=${encodeURIComponent(s.id)}&token=${encodeURIComponent(s.token)}`;
}

async function copyLink(button, s) {
  const link = privateLink(s);
  try {
    await navigator.clipboard.writeText(link);
    button.textContent = "Copied";
    setTimeout(() => {
      button.textContent = "Copy link";
    }, 1500);
  } catch {
    window.prompt("Copy this private link:", link); // no clipboard access (plain http)
  }
}

function renderTicket({ s, req, error }) {
  const li = $("ticket-tpl").content.firstElementChild.cloneNode(true);
  const status = li.querySelector(".t-status");
  const meta = li.querySelector(".t-meta");
  const question = li.querySelector(".t-question");
  const reply = li.querySelector(".t-reply");
  li.querySelector(".t-forget").addEventListener("click", () => {
    forget(s.id);
    void refresh();
  });
  li.querySelector(".t-link").addEventListener("click", (ev) => void copyLink(ev.currentTarget, s));

  if (!req) {
    status.textContent = error ? "Error" : "Gone";
    status.classList.add("muted");
    question.textContent = error
      ? `Couldn’t load this question: ${error}`
      : "This question is no longer available: it was deleted, or the key was revoked.";
    return li;
  }
  question.textContent = req.question;
  status.classList.add(req.status);
  if (req.status === "pending") {
    status.textContent = "Waiting";
    meta.textContent = req.delivered
      ? `Sent ${ago(req.created)} · ${toName()} has it`
      : `Sent ${ago(req.created)} · not picked up yet`;
  } else if (req.status === "replied") {
    status.textContent = "Answered";
    meta.textContent = `Sent ${ago(req.created)} · answered ${ago(req.answered)}`;
    setText(reply, req.reply);
  } else {
    status.textContent = "Declined";
    meta.textContent = `Sent ${ago(req.created)} · declined ${ago(req.answered)}`;
    setText(reply, req.reply || `${toName()} declined this question.`);
  }
  return li;
}

async function refresh() {
  if (!state.key) return;
  const list = saved();
  $("mine-empty").hidden = list.length > 0;
  let lost = false;
  const items = await Promise.all(
    list.map(async (s) => {
      try {
        return { s, req: await call(`/api/requests/${encodeURIComponent(s.id)}`, { token: s.token }) };
      } catch (e) {
        if (e.status === 401) lost = true;
        return { s, req: null, error: e.status === 404 ? null : e.message };
      }
    }),
  );
  if (lost) {
    lostKey("This key no longer works: it was revoked.");
    return;
  }
  $("mine-list").replaceChildren(...items.map(renderTicket));
}

/* ------------------------------------------------------------ wiring */

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  return { key: params.get("key"), request: params.get("request"), token: params.get("token") };
}

async function init() {
  $("key-show").addEventListener("click", () => {
    const input = $("key-input");
    input.type = input.type === "password" ? "text" : "password";
    $("key-show").textContent = input.type === "password" ? "Show" : "Hide";
  });

  $("key-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const key = $("key-input").value.trim();
    if (!key) return;
    $("key-open").disabled = true;
    await openKey(key);
    $("key-open").disabled = false;
  });

  $("key-forget").addEventListener("click", () => lostKey(""));
  $("refresh").addEventListener("click", () => void refresh());

  $("ask-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const send = $("ask-send");
    setText($("ask-error"), "");
    setText($("ask-ok"), "");
    const name = $("ask-name").value.trim();
    send.disabled = true;
    try {
      const sent = await call("/api/requests", {
        method: "POST",
        body: { question: $("ask-question").value.trim(), name },
      });
      remember(sent.id, sent.token);
      store(NAME_STORE, name || null);
      $("ask-question").value = "";
      setText($("ask-ok"), `Sent. ${toName()}’s answer will show up below.`);
      await refresh();
    } catch (e) {
      if (e.status === 401) lostKey("This key no longer works: it was revoked.");
      else setText($("ask-error"), e.status === 429 ? "You’ve asked a lot already. Please try again later." : e.message);
    } finally {
      send.disabled = false;
    }
  });

  // Links: #key=<key> (from the person who gave it) or #request=<id>&token=<token> (a question's private link).
  const hash = readHash();
  if (hash.request && hash.token) state.pendingTicket = { id: hash.request, token: hash.token };
  if (location.hash) history.replaceState(null, "", location.pathname); // keep secrets out of the address bar
  const key = hash.key || load(KEY_STORE);
  if (key) {
    $("key-input").value = key;
    await openKey(key, { quiet: !hash.key });
  } else if (state.pendingTicket) {
    setText($("key-error"), "Enter the key you used to ask this question.");
  }
}

void init();
