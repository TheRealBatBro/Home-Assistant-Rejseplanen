/*
 * Rejseplanen Live card: a DSB-style departure board for the integration's
 * "Next departure" sensors (station boards and trips).
 *
 *   type: custom:rejseplanen-live-card
 *   entity: sensor.roskilde_st_next_departure
 *   rows: 6              # optional
 *   title: Roskilde      # optional
 *   show_messages: true  # optional, traffic messages ticker
 *   show_notes: false    # optional, facilities (bikes, wifi...)
 *   theme: board         # "board" (dark DSB look) or "card" (follows HA theme)
 */

const TEXT = {
  da: {
    departures: "Afgange", arrivals: "Ankomster", time: "Tid", line: "Linje", to: "Til",
    from: "Fra", track: "Spor", status: "Bemærkning", cancelled: "Aflyst",
    partly: "Delvist aflyst", newTime: "Ny tid", newTrack: "Nyt spor", now: "Nu", min: "min",
    none: "Ingen afgange", onTime: "", extra: "Ekstra", changes: "skift", direct: "Direkte",
    risk: "Forbindelse i fare", leaveBy: "Gå", noEntity: "Vælg en 'Næste afgang'-sensor",
    arrival: "Ankomst", in: "om",
  },
  en: {
    departures: "Departures", arrivals: "Arrivals", time: "Time", line: "Line", to: "To",
    from: "From", track: "Track", status: "Remarks", cancelled: "Cancelled",
    partly: "Part cancelled", newTime: "New time", newTrack: "New track", now: "Now", min: "min",
    none: "No departures", onTime: "", extra: "Extra", changes: "changes", direct: "Direct",
    risk: "Connection at risk", leaveBy: "Leave", noEntity: "Pick a 'Next departure' sensor",
    arrival: "Arrival", in: "in",
  },
};

const S_TRAIN = { A: "#0095da", B: "#4db848", BX: "#9ec73d", C: "#f7941d", E: "#8f65a8", F: "#ffc20e", H: "#e5242c" };
const METRO = { M1: "#00815b", M2: "#fec211", M3: "#ed1c24", M4: "#0091d3" };
const TYPE_COLOR = {
  ic: "#c8102e", lyn: "#c8102e", regional: "#c8102e", other_train: "#6d6e71",
  bus: "#ffb000", express_bus: "#0072bc", night_bus: "#3d3d8f", flex_bus: "#8a8a8a",
  ferry: "#00739a", light_rail: "#3a8dde", metro: "#00815b", flight: "#555",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function hhmm(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Copenhagen" });
}

function badge(type, line, category) {
  let label = line || category || "";
  let color = TYPE_COLOR[type] || "#6d6e71";
  let fg = "#fff";
  if (type === "s_train") {
    label = String(line || category || "");
    const key = label.toUpperCase();
    color = S_TRAIN[key] || "#b41f3a";
    if (key === "F") fg = "#000";
  } else if (type === "metro") {
    const key = String(line || "").toUpperCase().replace(/^METRO\s*/, "");
    label = key;
    color = METRO[key] || color;
    if (key === "M2") fg = "#000";
  } else if (["ic", "lyn", "regional"].includes(type)) {
    label = category || label;
  } else if (type === "other_train") {
    label = /lokalbane/i.test(category || "") ? String(line || "L").replace(/^Lokalbane\s*/i, "") : category || label;
  } else if (type === "bus") {
    fg = "#000";
  }
  return `<span class="badge" style="background:${color};color:${fg}">${esc(label)}</span>`;
}

class RejseplanenLiveCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this._config = { rows: 6, show_messages: true, show_notes: false, theme: "board", ...config };
    this._lastState = undefined;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const state = hass.states[this._config.entity];
    if (state !== this._lastState) {
      this._lastState = state;
      this._render();
    }
  }

  connectedCallback() {
    this._timer = setInterval(() => this._render(), 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 2 + Math.min(this._config?.rows ?? 6, 10);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && (hass.states[id].attributes.departures || hass.states[id].attributes.trips)
    );
    return { entity: entity || "" };
  }

  _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "da").slice(0, 2);
    return TEXT[lang] || TEXT.en;
  }

  _countdown(iso, t) {
    if (!iso) return "";
    const mins = Math.ceil((new Date(iso) - Date.now()) / 60000);
    if (mins <= 0) return t.now;
    if (mins >= 60) return "";
    return `${mins} ${t.min}`;
  }

  _status(row, t) {
    const parts = [];
    if (row.cancelled) parts.push(`<span class="bad">${t.cancelled}</span>`);
    else if (row.partially_cancelled) parts.push(`<span class="bad">${t.partly}</span>`);
    if (!row.cancelled && row.delay && row.delay > 0) parts.push(`<span class="warn">${t.newTime} ${hhmm(row.expected)}</span>`);
    if (row.track_changed) parts.push(`<span class="warn">${t.newTrack} ${esc(row.track)}</span>`);
    if (row.reachable === false) parts.push(`<span class="warn">${t.risk}</span>`);
    if (row.extra) parts.push(`<span class="info">${t.extra}</span>`);
    return parts.join(" ");
  }

  _boardRows(attrs, t) {
    // Like the boards on the platform: scheduled order, new times shown per row.
    const rows = [...(attrs.departures || [])]
      .sort((a, b) => new Date(a.scheduled) - new Date(b.scheduled))
      .slice(0, this._config.rows);
    if (!rows.length) return `<div class="empty">${t.none}</div>`;
    const arrivals = attrs.board === "arrivals";
    const head = `<div class="row head"><span>${t.time}</span><span></span><span>${arrivals ? t.from : t.to}</span><span class="c">${t.track}</span></div>`;
    const body = rows
      .map((row) => {
        const cls = row.cancelled ? "row cancelled" : "row";
        const notes = this._config.show_notes && row.notes?.length ? `<div class="notes">${row.notes.map(esc).join(" · ")}</div>` : "";
        const status = this._status(row, t);
        const countdown = this._countdown(row.expected, t);
        return `<div class="${cls}">
          <span class="time"><b>${hhmm(row.scheduled)}</b>${countdown ? `<small>${countdown}</small>` : ""}</span>
          <span>${badge(row.type, row.line, row.category)}</span>
          <span class="dest"><span class="name">${esc(row.direction)}</span>${status ? `<div class="status">${status}</div>` : ""}${notes}</span>
          <span class="c track ${row.track_changed ? "warn" : ""}">${esc(row.track || "")}</span>
        </div>`;
      })
      .join("");
    return head + body;
  }

  _tripRows(attrs, t) {
    const trips = (attrs.trips || []).slice(0, this._config.rows);
    if (!trips.length) return `<div class="empty">${t.none}</div>`;
    return trips
      .map((trip) => {
        const legs = trip.legs
          .filter((leg) => leg.line || leg.category)
          .map((leg) => badge(leg.product_type, leg.line, leg.category))
          .join('<span class="arrow">›</span>');
        const dep = trip.departure;
        const arr = trip.arrival;
        const status = [];
        if (trip.cancelled) status.push(`<span class="bad">${t.cancelled}</span>`);
        if (!trip.reachable) status.push(`<span class="warn">${t.risk}</span>`);
        if (!trip.cancelled && dep.delay > 0) status.push(`<span class="warn">+${dep.delay} ${t.min}</span>`);
        if (dep.track_changed) status.push(`<span class="warn">${t.newTrack} ${esc(dep.track)}</span>`);
        const changes = trip.changes ? `${trip.changes} ${t.changes}` : t.direct;
        const countdown = this._countdown(dep.expected, t);
        return `<div class="trip ${trip.cancelled ? "cancelled" : ""}">
          <span class="time"><b>${hhmm(dep.expected)}</b>${countdown ? `<small>${countdown}</small>` : ""}</span>
          <span class="legs">${legs}<div class="sub">${t.arrival} ${hhmm(arr.expected)}${arr.delay > 0 ? ` (+${arr.delay})` : ""} · ${trip.duration ?? "?"} ${t.min} · ${changes}${dep.track ? ` · ${t.track} ${esc(dep.track)}` : ""}</div></span>
          <span class="r">${status.join(" ")}</span>
        </div>`;
      })
      .join("");
  }

  _render() {
    if (!this.shadowRoot || !this._hass || !this._config) return;
    const t = this._t();
    const state = this._hass.states[this._config.entity];
    if (!state) {
      this.shadowRoot.innerHTML = `<ha-card><div class="empty">${t.noEntity}</div></ha-card>`;
      return;
    }
    const attrs = state.attributes;
    const isTrip = Array.isArray(attrs.trips);
    const title =
      this._config.title ||
      (isTrip
        ? `${attrs.departure?.name || ""} → ${attrs.arrival?.name || ""}`
        : `${attrs.board === "arrivals" ? t.arrivals : t.departures} · ${attrs.stop || ""}`);
    const messages = this._config.show_messages ? attrs.messages || [] : [];
    const ticker = messages.length
      ? (() => {
          const text = messages.map((m) => `<b>${esc(m.head)}</b> ${esc(m.text)}`).join(" &nbsp;•&nbsp; ");
          return `<div class="ticker"><div class="scroll"><span>${text}</span><span aria-hidden="true">${text}</span></div></div>`;
        })()
      : "";
    const clock = new Date().toLocaleTimeString("da-DK", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Copenhagen" });
    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card class="${this._config.theme === "card" ? "plain" : "board"}">
        <div class="top"><span class="title">${esc(title)}</span><span class="clock">${clock}</span></div>
        ${ticker}
        <div class="rows">${isTrip ? this._tripRows(attrs, t) : this._boardRows(attrs, t)}</div>
      </ha-card>`;
  }

  _css() {
    return `
      ha-card { overflow: hidden; }
      ha-card.board { --bg: #001e3c; --fg: #ffffff; --muted: #9fb3c8; --line: rgba(255,255,255,.08); --warn: #ffd200; --bad: #ff6b6b; background: var(--bg); color: var(--fg); }
      ha-card.plain { --bg: var(--card-background-color); --fg: var(--primary-text-color); --muted: var(--secondary-text-color); --line: var(--divider-color); --warn: var(--warning-color, #c77c00); --bad: var(--error-color, #db4437); }
      .top { display: flex; justify-content: space-between; align-items: baseline; padding: 14px 16px 8px; }
      .title { font-size: 1.15em; font-weight: 600; letter-spacing: .01em; }
      .clock { font-variant-numeric: tabular-nums; font-size: 1.15em; color: var(--warn); font-weight: 600; }
      .ticker { overflow: hidden; white-space: nowrap; background: var(--warn); color: #000; font-size: .85em; padding: 4px 0; }
      .ticker .scroll { display: inline-flex; animation: scroll 40s linear infinite; }
      .ticker .scroll span { padding: 0 48px 0 16px; }
      @keyframes scroll { to { transform: translateX(-50%); } }
      @media (prefers-reduced-motion: reduce) { .ticker { white-space: normal; } .ticker .scroll { animation: none; } .ticker .scroll span + span { display: none; } }
      .rows { padding: 0 8px 10px; }
      .row { display: grid; grid-template-columns: 52px auto minmax(0, 1fr) 40px; gap: 10px; align-items: center; padding: 7px 8px; border-bottom: 1px solid var(--line); }
      .row > span:nth-child(2) { min-width: 34px; }
      .status { font-size: .8em; margin-top: 1px; white-space: normal; }
      .row.head { color: var(--muted); font-size: .75em; text-transform: uppercase; letter-spacing: .06em; padding-top: 2px; }
      .trip { display: grid; grid-template-columns: 58px 1fr minmax(0, auto); gap: 8px; align-items: center; padding: 8px; border-bottom: 1px solid var(--line); }
      .time b { display: block; font-variant-numeric: tabular-nums; font-size: 1.05em; }
      .time small { color: var(--muted); font-size: .75em; }
      .dest { min-width: 0; }
      .dest .name { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
      .notes, .sub { color: var(--muted); font-size: .75em; white-space: normal; margin-top: 2px; }
      .c { text-align: center; }
      .r { text-align: right; font-size: .85em; }
      .track { font-weight: 600; font-variant-numeric: tabular-nums; }
      .badge { display: inline-block; min-width: 26px; padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: .8em; text-align: center; }
      .legs .badge { margin-right: 2px; }
      .arrow { color: var(--muted); margin: 0 3px; }
      .warn { color: var(--warn); font-weight: 600; }
      .bad { color: var(--bad); font-weight: 700; }
      .info { color: var(--muted); }
      .cancelled .dest .name, .cancelled .time b, .cancelled .legs .badge { text-decoration: line-through; opacity: .6; }
      .empty { padding: 18px 16px; color: var(--muted); }

    `;
  }
}

function register() {
  // Read the registry at call time: it is replaced during frontend boot.
  const registry = window.customElements;
  if (!registry.get("rejseplanen-live-card")) {
    registry.define("rejseplanen-live-card", RejseplanenLiveCard);
  }
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "rejseplanen-live-card")) {
    window.customCards.push({
      type: "rejseplanen-live-card",
      name: "Rejseplanen Live",
      description: "Departure board with delays, track changes, cancellations and traffic messages.",
      preview: true,
    });
  }
}

// This script is loaded as an extra module and can run before Home Assistant's
// frontend has installed its scoped custom element registry. A card defined in
// the native registry at that point is invisible to Lovelace ("Custom element
// doesn't exist"), so wait until the frontend's own elements are defined.
Promise.race([
  customElements.whenDefined("ha-card"),
  new Promise((resolve) => setTimeout(resolve, 15000)),
]).then(register);
