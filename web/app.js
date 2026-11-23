/* Transimate Assistant - web client */

const ROUTE_COLORS = {
  R1: "#2563eb", R2: "#16a34a", R3: "#d97706",
  R4: "#7c3aed", R5: "#dc2626",
};

const SAMPLE_QUESTIONS = [
  { q: "Which buses are running right now, and which one should I take to reach campus?", tag: "Student" },
  { q: "Where is my bus B1 now, and when is it expected to reach Library?", tag: "Student" },
  { q: "How many seats are available on my bus right now, and is it likely to be overcrowded?", tag: "Student" },
  { q: "Is my bus delayed or cancelled right now, and what is the reason?", tag: "Student" },
  { q: "Is there any traffic, roadblock, or route diversion currently affecting my journey?", tag: "Student" },
  { q: "My regular bus on R1 is unavailable. Which alternative bus can I take right now?", tag: "Student" },
  { q: "How many students are currently waiting at each pickup stop?", tag: "Student" },
  { q: "Which routes are overcrowded right now, and which nearby routes have spare capacity?", tag: "Student" },
  { q: "Has my child's bus reached the pickup point, and is it running according to schedule?", tag: "Parent" },
  { q: "Is there any current safety issue, breakdown, or unexpected change affecting my child's bus?", tag: "Parent" },
  { q: "Which routes are experiencing the longest delays today, and what are the main reasons?", tag: "Admin" },
  { q: "Which buses are currently unavailable due to maintenance or other issues, and which routes are affected?", tag: "Admin" },
  { q: "Based on the number of students waiting and available seats, where is an additional bus needed right now?", tag: "Admin" },
  { q: "If a bus breaks down right now, which available bus can take its passengers with the least disruption?", tag: "Admin" },
  { q: "Based on current demand, bus capacity, delays, and route conditions, how should the buses be redistributed right now?", tag: "Admin" },
];

let state = null;
let es = null;

// ---------- SSE live stream ----------
function connect() {
  es = new EventSource("/api/events");
  es.onmessage = (e) => {
    state = JSON.parse(e.data);
    document.getElementById("clock").textContent = state.clock;
    renderMap();
    renderAdmin();
  };
  es.onerror = () => {
    document.getElementById("liveBadge").style.color = "var(--bad)";
    setTimeout(connect, 3000);
  };
}

// ---------- Chat ----------
function addMsg(role, html) {
  const chat = document.getElementById("chat");
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.innerHTML = html;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

async function ask(q) {
  if (!q.trim()) return;
  addMsg("user", `<div class="bubble">${esc(q)}</div>`);
  document.getElementById("input").value = "";
  const persona = document.getElementById("persona").value;
  const typing = addMsg("bot", `<div class="bubble typing">Consulting the live transport feed…</div>`);
  try {
    const r = await fetch("/api/chat?q=" + encodeURIComponent(q) + "&persona=" + persona);
    const data = await r.json();
    typing.remove();
    const reasoning = (data.reasoning || [])
      .map((s) => `<li>${esc(s)}</li>`).join("");
    const chips = (data.suggestions || [])
      .map((s) => `<button class="chip" data-q="${esc(s)}">${esc(s)}</button>`).join("");
    addMsg("bot", `
      <div class="bubble">
        <span class="cap-badge">${esc(data.capabilityLabel || data.capability || "")}</span>
        <div>${esc(data.reply)}</div>
      </div>
      ${reasoning ? `<details class="reasoning"><summary>Reasoning trace</summary><ol>${reasoning}</ol></details>` : ""}
      ${chips ? `<div class="chips">${chips}</div>` : ""}
    `);
    bindChips();
  } catch (err) {
    typing.remove();
    addMsg("bot", `<div class="bubble">Sorry, I could not reach the agent. Is the server running?</div>`);
  }
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function bindChips() {
  document.querySelectorAll(".chip").forEach((b) => {
    b.onclick = () => ask(b.dataset.q);
  });
}

// ---------- Live map (SVG) ----------
function renderMap() {
  const svg = document.getElementById("map");
  if (!state) return;
  svg.innerHTML = "";

  // grid background
  for (let x = 0; x <= 1000; x += 100) {
    const l = document.createElementNS("http://www.w3.org/2000/svg", "line");
    l.setAttribute("x1", x); l.setAttribute("y1", 0); l.setAttribute("x2", x); l.setAttribute("y2", 700);
    l.setAttribute("stroke", "#1e293b"); l.setAttribute("stroke-width", 1);
    svg.appendChild(l);
  }
  for (let y = 0; y <= 700; y += 100) {
    const l = document.createElementNS("http://www.w3.org/2000/svg", "line");
    l.setAttribute("x1", 0); l.setAttribute("y1", y); l.setAttribute("x2", 1000); l.setAttribute("y2", y);
    l.setAttribute("stroke", "#1e293b"); l.setAttribute("stroke-width", 1);
    svg.appendChild(l);
  }

  // traffic zones
  (state.traffic_zones || []).forEach((tz) => {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", tz.pos[0]); c.setAttribute("cy", tz.pos[1]);
    c.setAttribute("r", tz.radius);
    c.setAttribute("fill", tz.severity === "heavy" ? "rgba(248,113,113,0.22)" : "rgba(251,191,36,0.18)");
    c.setAttribute("stroke", tz.severity === "heavy" ? "#f87171" : "#fbbf24");
    c.setAttribute("stroke-dasharray", "6 4");
    svg.appendChild(c);
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", tz.pos[0]); t.setAttribute("y", tz.pos[1] - tz.radius - 6);
    t.setAttribute("fill", "#fbbf24"); t.setAttribute("font-size", 12); t.setAttribute("text-anchor", "middle");
    t.textContent = tz.reason;
    svg.appendChild(t);
  });

  // stops with waiting counts
  const stopCounts = state.waiting || {};
  const stopPositions = window.STOP_POSITIONS || {};
  Object.entries(stopPositions).forEach(([sid, pos]) => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", pos[0]); c.setAttribute("cy", pos[1]);
    c.setAttribute("r", 7);
    c.setAttribute("fill", "#0f172a");
    c.setAttribute("stroke", "#94a3b8");
    c.setAttribute("stroke-width", 2);
    g.appendChild(c);
    const waiting = stopCounts[sid] || 0;
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", pos[0] + 11); t.setAttribute("y", pos[1] + 4);
    t.setAttribute("fill", waiting >= 8 ? "#f87171" : waiting >= 4 ? "#fbbf24" : "#94a3b8");
    t.setAttribute("font-size", 12);
    t.textContent = `${window.STOP_NAMES?.[sid] || sid} (${waiting})`;
    g.appendChild(t);
    svg.appendChild(g);
  });

  // buses
  state.buses.forEach((b) => {
    const [x, y] = b.position;
    const color = b.status === "breakdown" ? "#f87171"
      : b.status === "delayed" ? "#fbbf24" : ROUTE_COLORS[b.route] || "#38bdf8";
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("transform", `translate(${x},${y})`);
    g.style.cursor = "pointer";
    const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    r.setAttribute("x", -11); r.setAttribute("y", -8);
    r.setAttribute("width", 22); r.setAttribute("height", 16);
    r.setAttribute("rx", 4);
    r.setAttribute("fill", color);
    r.setAttribute("stroke", "#0f172a");
    r.setAttribute("stroke-width", 2);
    g.appendChild(r);
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", 0); t.setAttribute("y", 4);
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("font-size", 10); t.setAttribute("font-weight", 700);
    t.setAttribute("fill", "#0f172a");
    t.textContent = b.id;
    g.appendChild(t);
    g.addEventListener("click", () => {
      document.querySelector(`.bus-card[data-bus="${b.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    svg.appendChild(g);
  });

  renderLegend();
  renderBusCards();
}

function renderLegend() {
  const el = document.getElementById("mapLegend");
  if (!el || el.dataset.done) return;
  el.dataset.done = "1";
  el.innerHTML = Object.entries(ROUTE_COLORS).map(([r, c]) =>
    `<div class="row"><span class="swatch" style="background:${c}"></span>Route ${r}</div>`
  ).join("") +
    `<div class="row"><span class="swatch" style="background:#fbbf24"></span>Delayed</div>` +
    `<div class="row"><span class="swatch" style="background:#f87171"></span>Broken down</div>`;
}

function renderBusCards() {
  const panel = document.getElementById("busPanel");
  if (!panel || !state) return;
  panel.innerHTML = state.buses.map((b) => {
    const pct = Math.min(130, Math.round((b.occupancy / b.capacity) * 100));
    const barColor = pct >= 100 ? "var(--bad)" : pct >= 80 ? "var(--warn)" : "var(--ok)";
    const statusLabel = b.status === "delayed" ? `+${b.delay_min}m` : b.status;
    return `<div class="bus-card" data-bus="${b.id}">
      <h3>${b.id} · ${b.route} <small style="color:var(--muted);font-weight:400">${statusLabel}</small></h3>
      <div class="row"><span>Next: ${esc(b.next_stop)}</span><span>${b.occupancy}/${b.capacity}</span></div>
      <div class="bar"><i style="width:${pct}%;background:${barColor}"></i></div>
      <div class="row"><span>${b.driver}</span><span>${pct}%</span></div>
    </div>`;
  }).join("");
}

// ---------- Admin ----------
function renderAdmin() {
  const grid = document.getElementById("adminGrid");
  if (!grid || !state) return;
  const routes = state.routes || {};
  const rows = Object.entries(routes).map(([rid, st]) => {
    const cls = st.status === "ok" ? "status-ok" : st.status === "delayed" ? "status-delayed" : "status-disrupted";
    return `<tr><td><b>${rid}</b></td><td>${st.buses}</td><td class="${cls}">${st.status}</td>
      <td>${Math.round(st.avg_load * 100)}%</td><td>${st.delayed}</td><td>${st.broken}</td></tr>`;
  }).join("");

  const totalWaiting = Object.values(state.waiting || {}).reduce((a, b) => a + b, 0);
  const running = state.buses.filter((b) => b.status === "running" || b.status === "delayed").length;
  const delayed = state.buses.filter((b) => b.status === "delayed").length;
  const broken = state.buses.filter((b) => b.status === "breakdown").length;

  grid.innerHTML = `
    <div class="admin-card">
      <h3>Fleet overview · ${state.clock}</h3>
      <div class="stat-row">
        <div class="stat"><b>${running}</b><span>buses running</span></div>
        <div class="stat"><b>${delayed}</b><span>delayed</span></div>
        <div class="stat"><b>${broken}</b><span>broken down</span></div>
        <div class="stat"><b>${totalWaiting}</b><span>students waiting</span></div>
      </div>
      <table>
        <tr><th>Route</th><th>Buses</th><th>Status</th><th>Avg load</th><th>Delayed</th><th>Broken</th></tr>
        ${rows}
      </table>
    </div>
    <div class="admin-card">
      <h3>Recommended actions</h3>
      <div id="actions"></div>
    </div>
  `;
  fetch("/api/admin").then((r) => r.json()).then((d) => {
    const el = document.getElementById("actions");
    if (!el) return;
    if (d.actions) {
      el.innerHTML = `<ul class="action-list">${d.actions.map((a) => `
        <li><span class="prio prio-${a.priority || "low"}">${a.priority || "low"}</span>${esc(a.text || a.reason || JSON.stringify(a))}</li>
      `).join("")}</ul>`;
    }
    if (d.additional_bus) {
      const ab = d.additional_bus;
      el.innerHTML += `<div class="reco" style="margin-top:12px">
        <b>Extra bus needed:</b> ${esc(ab.stop_name)} — ${ab.waiting} students waiting, only ~${ab.spare} spare seats on ${ab.route}.
      </div>`;
    }
  }).catch(() => {});
}

// ---------- Init ----------
function init() {
  const s = document.getElementById("suggestions");
  s.innerHTML = SAMPLE_QUESTIONS.map(({ q, tag }) =>
    `<button data-q="${esc(q)}"><b style="color:var(--accent);font-size:11px">${tag.toUpperCase()}</b><br/>${esc(q)}</button>`
  ).join("");
  s.querySelectorAll("button").forEach((b) => b.onclick = () => ask(b.dataset.q));

  document.getElementById("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    ask(document.getElementById("input").value);
  });

  document.querySelectorAll(".tab").forEach((t) => t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    document.getElementById("tab-" + t.dataset.tab).classList.add("active");
  });

  connect();
  ask("Which buses are running right now, and which one should I take to reach campus?");
}

window.addEventListener("load", () => {
  // stop metadata injected by server via /api/stops
  fetch("/api/stops").then((r) => r.json()).then((d) => {
    window.STOP_POSITIONS = d.positions;
    window.STOP_NAMES = d.names;
    renderMap();
  });
  init();
});