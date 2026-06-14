import {
    addSpot, addSpotBatch, clearMap,
    setDisplayMaxAge, setShowLines, setDarkMode, setDotAtRx, setMonitorsMode,
    getActiveSpotCount, BAND_COLORS,
} from "./map.js";

/* Dot placement: monitors and "sent by" both plot at RX grid */
function applyDotMode(ct) {
    setMonitorsMode(ct === "monitors");
    setDotAtRx(ct === "monitors" || ct === "tx");
}

/* ---- Preferences (localStorage) ------------------------------------------ */

const _LS_KEY = "pskr-map-prefs";

function savePrefs() {
    try {
        localStorage.setItem(_LS_KEY, JSON.stringify({
            display: {
                bands:            selectedBands(),
                modes:            selectedModes(),
                max_age_minutes:  maxAgeMinutes(),
                call_type:        document.getElementById("call-type").value,
                call_value:       document.getElementById("call-filter").value.trim(),
            },
            options: {
                show_lines: document.getElementById("show-lines").checked,
                dark_mode:  document.getElementById("dark-mode").checked,
            },
            feed: {
                bands:       selectedFeedBands(),
                modes:       selectedFeedModes(),
                ttl_minutes: feedTtlMinutes(),
                call_value:  document.getElementById("feed-call-filter").value.trim(),
            },
        }));
    } catch (_) {}
}

function loadPrefs() {
    try {
        const raw = localStorage.getItem(_LS_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
}

function applyPrefsToUi(prefs) {
    if (!prefs) return;

    const d = prefs.display || {};
    if (d.bands) setChecked("#band-list input", d.bands);
    if (d.modes) setChecked("#mode-list input", d.modes);
    if (d.max_age_minutes != null) {
        document.getElementById("max-age").value = d.max_age_minutes;
        document.getElementById("max-age-label").textContent = d.max_age_minutes + " min";
        setDisplayMaxAge(d.max_age_minutes);
    }
    const callType  = document.getElementById("call-type");
    const callValue = document.getElementById("call-filter");
    callType.value  = d.call_type || "tx";
    callValue.value = d.call_value || "";
    applyDotMode(callType.value);

    const o = prefs.options || {};
    const lines    = !!o.show_lines;
    const darkMode = !!o.dark_mode;
    document.getElementById("show-lines").checked = lines;
    document.getElementById("dark-mode").checked  = darkMode;
    setShowLines(lines);
    document.getElementById("snr-legend").style.display = lines ? "block" : "none";
    document.body.classList.toggle("dark-mode", darkMode);
    setDarkMode(darkMode);

    const f = prefs.feed || {};
    if (f.bands) setChecked("#feed-band-list input", f.bands);
    if (f.modes) setChecked("#feed-mode-list input", f.modes);
    if (f.ttl_minutes != null) {
        document.getElementById("feed-ttl").value = f.ttl_minutes;
        document.getElementById("feed-ttl-label").textContent = f.ttl_minutes + " min";
    }
    document.getElementById("feed-call-filter").value = f.call_value || "";

    updateDisplayBadges();
    updateFeedBadge();
}

/* ---- WebSocket ----------------------------------------------------------- */

let ws;
let reconnectTimer;
let _skipNextFilterAck = false;

function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
        setStatus("connected");
        clearTimeout(reconnectTimer);
        // Push our saved display filter so the server re-queries with the right params.
        // Skip the server's stale bootstrap filter_ack that arrives before our set_filter.
        if (loadPrefs()) {
            _skipNextFilterAck = true;
            sendFilter();
        }
    };

    ws.onclose = () => {
        setStatus("disconnected");
        reconnectTimer = setTimeout(connect, 5000);
    };

    ws.onerror = () => { ws.close(); };

    ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        switch (msg.type) {
            case "spot":
                addSpot(msg.data);
                setDisplayCount(getActiveSpotCount());
                break;
            case "spot_batch":
                clearMap();
                addSpotBatch(msg.data);
                setDisplayCount(msg.data.length);
                break;
            case "filter_ack":
                if (_skipNextFilterAck) {
                    _skipNextFilterAck = false;
                } else {
                    applyFilterUi(msg.filter);
                }
                break;
            case "feed_ack":
                applyFeedUi(msg.feed);
                break;
            case "stats":
                setDbCount(msg.data.db_total);
                setRate(msg.data.spots_last_min);
                break;
        }
    };
}

/* Display filter: re-queries SQLite only, no MQTT change */
function sendFilter() {
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({
        type: "set_filter",
        bands: selectedBands(),
        modes: selectedModes(),
        display_max_age_minutes: maxAgeMinutes(),
        call_type:  document.getElementById("call-type").value,
        call_value: document.getElementById("call-filter").value.trim(),
    }));
}

/* Feed filter: changes MQTT subscription and/or DB TTL */
function sendFeed() {
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({
        type: "set_feed",
        bands: selectedFeedBands(),
        modes: selectedFeedModes(),
        ttl_minutes: feedTtlMinutes(),
        call_value: document.getElementById("feed-call-filter").value.trim(),
    }));
}

/* ---- Band / mode data ---------------------------------------------------- */

const BANDS = [
    "160m", "80m", "60m", "40m", "30m", "20m",
    "17m",  "15m", "12m", "10m", "6m",  "2m", "70cm",
];

const MODES = [
    "FT8", "FT4", "CW", "JS8", "WSPR",
    "PSK31", "RTTY", "JT9", "JT65", "Q65",
];

/* ---- Checkbox builders --------------------------------------------------- */

function buildCheckboxGroup(containerId, items, defaultSelected, onChange) {
    const el = document.getElementById(containerId);
    for (const item of items) {
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = item;
        cb.checked = defaultSelected.includes(item);
        cb.addEventListener("change", onChange);
        label.appendChild(cb);
        label.appendChild(document.createTextNode(" " + item));
        el.appendChild(label);
    }
}

function checkedValues(selector) {
    return Array.from(document.querySelectorAll(selector))
        .filter(cb => cb.checked).map(cb => cb.value);
}

function selectedBands()     { return checkedValues("#band-list input"); }
function selectedModes()     { return checkedValues("#mode-list input"); }
function selectedFeedBands() { return checkedValues("#feed-band-list input"); }
function selectedFeedModes() { return checkedValues("#feed-mode-list input"); }
function maxAgeMinutes()     { return parseInt(document.getElementById("max-age").value, 10); }
function feedTtlMinutes()    { return parseInt(document.getElementById("feed-ttl").value, 10); }

/* ---- Badges -------------------------------------------------------------- */

function updateBadge(listSel, badgeId, total) {
    const n = document.querySelectorAll(`${listSel} input:checked`).length;
    const el = document.getElementById(badgeId);
    el.textContent = n === total ? "All" : String(n);
    el.className = `badge${n === 0 ? " none" : ""}`;
}

function updateBandLegend() {
    const selected = new Set(selectedBands());
    const rows = document.getElementById("band-legend-rows");
    rows.innerHTML = "";
    for (const band of BANDS) {
        if (!selected.has(band)) continue;
        const row = document.createElement("div");
        row.className = "row";
        const dot = document.createElement("span");
        dot.className = "swatch";
        dot.style.background = BAND_COLORS[band] ?? "#888888";
        row.appendChild(dot);
        row.appendChild(document.createTextNode(" " + band));
        rows.appendChild(row);
    }
}

function updateDisplayBadges() {
    updateBadge("#band-list", "band-badge", BANDS.length);
    updateBadge("#mode-list", "mode-badge", MODES.length);
    updateBandLegend();
}

function updateFeedBadge() {
    updateBadge("#feed-band-list", "feed-badge", BANDS.length);
}

/* ---- Dropdowns ----------------------------------------------------------- */

function initDropdowns() {
    document.querySelectorAll(".dd-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const panel = btn.closest(".dd-wrap").querySelector(".dd-panel");
            const wasOpen = panel.classList.contains("open");
            closeAllDropdowns();
            if (!wasOpen) panel.classList.add("open");
        });
    });

    document.addEventListener("click", (e) => {
        if (!e.target.closest(".dd-wrap")) closeAllDropdowns();
    });

    bindQuickSelect("bands-all",  "#band-list input",  true,  onDisplayFilterChange);
    bindQuickSelect("bands-none", "#band-list input",  false, onDisplayFilterChange);
    bindQuickSelect("modes-all",  "#mode-list input",  true,  onDisplayFilterChange);
    bindQuickSelect("modes-none", "#mode-list input",  false, onDisplayFilterChange);

    bindQuickSelect("feed-bands-all",  "#feed-band-list input", true,  onFeedFilterChange);
    bindQuickSelect("feed-bands-none", "#feed-band-list input", false, onFeedFilterChange);
    bindQuickSelect("feed-modes-all",  "#feed-mode-list input", true,  onFeedFilterChange);
    bindQuickSelect("feed-modes-none", "#feed-mode-list input", false, onFeedFilterChange);
}

function bindQuickSelect(btnId, selector, checked, onChange) {
    document.getElementById(btnId).addEventListener("click", () => {
        document.querySelectorAll(selector).forEach(cb => cb.checked = checked);
        onChange();
    });
}

function closeAllDropdowns() {
    document.querySelectorAll(".dd-panel").forEach(p => p.classList.remove("open"));
}

/* ---- Filter change handlers ---------------------------------------------- */

let _displayDebounce;
function onDisplayFilterChange() {
    updateDisplayBadges();
    savePrefs();
    clearTimeout(_displayDebounce);
    _displayDebounce = setTimeout(sendFilter, 400);
}

let _feedDebounce;
function onFeedFilterChange() {
    updateFeedBadge();
    savePrefs();
    clearTimeout(_feedDebounce);
    _feedDebounce = setTimeout(sendFeed, 600);
}

/* ---- Apply server state to UI -------------------------------------------- */

function applyFilterUi(filter) {
    setChecked("#band-list input", filter.bands);
    setChecked("#mode-list input", filter.modes);
    document.getElementById("max-age").value = filter.display_max_age_minutes;
    document.getElementById("max-age-label").textContent =
        filter.display_max_age_minutes + " min";
    setDisplayMaxAge(filter.display_max_age_minutes);

    const typeEl  = document.getElementById("call-type");
    const valueEl = document.getElementById("call-filter");
    typeEl.value  = filter.call_type || "tx";
    valueEl.value = filter.call_value || "";
    applyDotMode(typeEl.value);

    updateDisplayBadges();
}

function applyFeedUi(feed) {
    setChecked("#feed-band-list input", feed.bands);
    setChecked("#feed-mode-list input", feed.modes);
    if (feed.ttl_minutes) {
        document.getElementById("feed-ttl").value = feed.ttl_minutes;
        document.getElementById("feed-ttl-label").textContent = feed.ttl_minutes + " min";
    }
    document.getElementById("feed-call-filter").value = feed.call_value || "";
    updateFeedBadge();
}

function setChecked(selector, values) {
    for (const cb of document.querySelectorAll(selector)) {
        cb.checked = values.includes(cb.value);
    }
}

/* ---- Status bar ---------------------------------------------------------- */

let _displayCount = 0;

// Keep the Showing counter in sync after each prune cycle (30s cadence matches map.js)
setInterval(() => setDisplayCount(getActiveSpotCount()), 30_000);

function setStatus(state) {
    const el = document.getElementById("status-conn");
    el.textContent = state === "connected" ? "● Connected" : "○ Disconnected";
    el.className = state;
}

function setDbCount(n) {
    document.getElementById("status-db").textContent = `DB: ${n.toLocaleString()} spots`;
}

function setDisplayCount(n) {
    _displayCount = n;
    document.getElementById("status-display").textContent = `Showing: ${n.toLocaleString()}`;
}

function setRate(n) {
    // ~150 bytes/spot × 8 bits × MQTT/TLS overhead ≈ 1400 bits/spot
    const kbps = (n * 1400 / 60 / 1000).toFixed(1);
    document.getElementById("status-rate").textContent = `${n}/min · ~${kbps} Kbps`;
}

/* ---- Init ---------------------------------------------------------------- */

window.addEventListener("DOMContentLoaded", () => {
    const defaults     = ["40m", "20m", "15m"];
    const defaultModes = ["FT8"];

    buildCheckboxGroup("band-list",      BANDS, defaults,     onDisplayFilterChange);
    buildCheckboxGroup("mode-list",      MODES, defaultModes, onDisplayFilterChange);
    buildCheckboxGroup("feed-band-list", BANDS, defaults,     onFeedFilterChange);
    buildCheckboxGroup("feed-mode-list", MODES, defaultModes, onFeedFilterChange);

    // Restore saved preferences before connecting so the UI is correct immediately
    applyPrefsToUi(loadPrefs());

    // Ensure dot mode matches the select even when there were no saved prefs
    applyDotMode(document.getElementById("call-type").value);

    initDropdowns();

    document.getElementById("max-age").addEventListener("input", (ev) => {
        document.getElementById("max-age-label").textContent = ev.target.value + " min";
        setDisplayMaxAge(parseInt(ev.target.value, 10));
        onDisplayFilterChange();
    });

    document.getElementById("feed-ttl").addEventListener("input", (ev) => {
        document.getElementById("feed-ttl-label").textContent = ev.target.value + " min";
        onFeedFilterChange();
    });

    document.getElementById("feed-call-filter").addEventListener("input", (ev) => {
        ev.target.value = ev.target.value.toUpperCase();
        const val = ev.target.value.trim();
        const valid = !val
            || /^[A-R]{2}\d{2}$/.test(val)              // exactly 4-char grid
            || /^[A-Z]{1,2}[0-9][A-Z]{1,3}$/.test(val); // standard callsign
        ev.target.classList.toggle("feed-call-invalid", !valid);
        if (valid) onFeedFilterChange();
    });

    // Station filter
    const callType  = document.getElementById("call-type");
    const callValue = document.getElementById("call-filter");

    callType.addEventListener("change", () => {
        applyDotMode(callType.value);
        onDisplayFilterChange();
    });

    callValue.addEventListener("input", (ev) => {
        ev.target.value = ev.target.value.toUpperCase();
        onDisplayFilterChange();
    });

    // Options
    document.getElementById("show-lines").addEventListener("change", (ev) => {
        setShowLines(ev.target.checked);
        document.getElementById("snr-legend").style.display = ev.target.checked ? "block" : "none";
        savePrefs();
    });

    document.getElementById("dark-mode").addEventListener("change", (ev) => {
        document.body.classList.toggle("dark-mode", ev.target.checked);
        setDarkMode(ev.target.checked);
        savePrefs();
    });

    connect();
});
