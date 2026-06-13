/* Maidenhead grid → [lat, lon] center point */
function gridToLatLon(grid) {
    if (!grid || grid.length < 4) return null;
    const g = grid.toUpperCase();
    const lon = (g.charCodeAt(0) - 65) * 20 - 180
              + parseInt(g[2]) * 2 + 1;
    const lat = (g.charCodeAt(1) - 65) * 10 - 90
              + parseInt(g[3]) * 1 + 0.5;
    if (grid.length >= 6) {
        const sub = grid.toLowerCase();
        return [
            lat + (sub.charCodeAt(5) - 97) / 24 + 1/48,
            lon + (sub.charCodeAt(4) - 97) / 12 + 1/24,
        ];
    }
    return [lat, lon];
}

/*
 * Snap a longitude to the world copy nearest the current map center.
 * Without this, a marker at 135°E placed while the map was Atlantic-centered
 * ends up projected off-screen when the user pans to the Pacific.
 */
function normalizeLon(lon) {
    const center = map.getCenter().lng;
    while (lon < center - 180) lon += 360;
    while (lon > center + 180) lon -= 360;
    return lon;
}

export const BAND_COLORS = {
    "160m": "#882200",  // dark maroon
    "80m":  "#ff4400",  // red
    "60m":  "#ff9900",  // orange
    "40m":  "#ffdd00",  // gold
    "30m":  "#00cc44",  // green
    "20m":  "#00aaff",  // sky blue
    "17m":  "#ee00cc",  // magenta
    "15m":  "#7744ff",  // violet
    "12m":  "#00ddaa",  // turquoise
    "10m":  "#ff5577",  // coral
    "6m":   "#bb88ff",  // lavender
    "2m":   "#88ccff",  // periwinkle
    "70cm": "#888888",  // gray
};

function bandToColor(band) { return BAND_COLORS[band] ?? "#888888"; }

function snrToColor(snr) {
    if (snr >= 10)  return "#ff3300";
    if (snr >= 0)   return "#ff9900";
    if (snr >= -10) return "#ffee00";
    if (snr >= -20) return "#00cc88";
    return "#3399ff";
}

function freqToMHz(hz) { return (hz / 1e6).toFixed(4); }

/*
 * Compute a great-circle arc as [lat, lon] points.
 * Longitudes are unwrapped across the antimeridian, then shifted so the
 * arc's start lands in the world copy nearest the current map center.
 * Using L.polyline with 50 steps instead of L.Geodesic gives us full
 * control over longitude wrapping and eliminates dateline truncation.
 */
function geodesicPoints(lat1, lon1, lat2, lon2, steps = 50) {
    const toRad = d => d * Math.PI / 180;
    const toDeg = r => r * 180 / Math.PI;

    const φ1 = toRad(lat1), λ1 = toRad(lon1);
    const φ2 = toRad(lat2), λ2 = toRad(lon2);

    const x1 = Math.cos(φ1)*Math.cos(λ1), y1 = Math.cos(φ1)*Math.sin(λ1), z1 = Math.sin(φ1);
    const x2 = Math.cos(φ2)*Math.cos(λ2), y2 = Math.cos(φ2)*Math.sin(λ2), z2 = Math.sin(φ2);

    const dot  = Math.min(1, Math.max(-1, x1*x2 + y1*y2 + z1*z2));
    const d    = Math.acos(dot);
    const sinD = Math.sin(d);

    const pts = [];
    for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        let x, y, z;
        if (sinD < 1e-10) {
            x = x1; y = y1; z = z1;
        } else {
            const A = Math.sin((1 - t) * d) / sinD;
            const B = Math.sin(t * d) / sinD;
            x = A*x1 + B*x2; y = A*y1 + B*y2; z = A*z1 + B*z2;
        }
        pts.push([toDeg(Math.asin(Math.max(-1, Math.min(1, z)))), toDeg(Math.atan2(y, x))]);
    }

    // Unwrap: prevent ±360° longitude jumps between consecutive points
    for (let i = 1; i < pts.length; i++) {
        const delta = pts[i][1] - pts[i - 1][1];
        if (delta >  180) pts[i][1] -= 360;
        if (delta < -180) pts[i][1] += 360;
    }

    // Shift the arc so its start falls in the world copy nearest the map center
    const target = normalizeLon(lon1);
    const shift  = Math.round((target - pts[0][1]) / 360) * 360;
    if (shift !== 0) for (const pt of pts) pt[1] += shift;

    return pts;
}

function ageLabel(ts) {
    const s = Math.floor(Date.now() / 1000) - ts;
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s/60)}m ago`;
    return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m ago`;
}

/* ---- Map setup ----------------------------------------------------------- */

const map = L.map("map", { center: [20, 0], zoom: 2 });

const _TILES = {
    light: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    dark:  "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
};
const _TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

const tileLayer = L.tileLayer(_TILES.light, {
    attribution: _TILE_ATTR,
    subdomains: "abcd",
    maxZoom: 19,
}).addTo(map);

export function setDarkMode(dark) {
    tileLayer.setUrl(dark ? _TILES.dark : _TILES.light);
}

/* ---- Spot layer management ----------------------------------------------- */

const spotLayer = L.layerGroup().addTo(map);

// seq → { layer, ts, expiry, data }
const activeSpots = new Map();

let displayMaxAgeSec = 30 * 60;
let showLines = false;

export function setDisplayMaxAge(minutes) { displayMaxAgeSec = minutes * 60; }

export function setShowLines(val) {
    showLines = val;
    redrawAll();
}

export function clearMap() {
    spotLayer.clearLayers();
    activeSpots.clear();
}

function makeLayer(spot) {
    const txRaw = gridToLatLon(spot.tx_grid);
    const rxRaw = gridToLatLon(spot.rx_grid);

    // Dots: snap to world copy nearest the current view
    const txLL = txRaw ? [txRaw[0], normalizeLon(txRaw[1])] : null;

    const dotColor = bandToColor(spot.band);
    const arcColor = snrToColor(spot.snr);
    const opacity  = 0.7;

    const label = [
        `<b>${spot.tx_call}</b> → <b>${spot.rx_call}</b>`,
        `${spot.mode} &nbsp; ${freqToMHz(spot.freq)} MHz`,
        `SNR ${spot.snr > 0 ? "+" : ""}${spot.snr} dB &nbsp; ${spot.band}`,
        ageLabel(spot.ts),
    ].join("<br>");

    if (showLines && txRaw && rxRaw) {
        const pts  = geodesicPoints(txRaw[0], txRaw[1], rxRaw[0], rxRaw[1]);
        const line = L.polyline(pts, { color: arcColor, weight: 1, opacity });
        line.bindTooltip(label, { sticky: true });
        return line;
    }

    if (txLL) {
        const dot = L.circleMarker(txLL, {
            radius: 3, color: dotColor, fillColor: dotColor, fillOpacity: opacity, weight: 1,
        });
        dot.bindTooltip(label, { sticky: true });
        return dot;
    }

    return null;
}

function redrawAll() {
    const entries = [...activeSpots.values()];
    spotLayer.clearLayers();
    activeSpots.clear();
    for (const { ts, expiry, data } of entries) {
        const layer = makeLayer(data);
        if (!layer) continue;
        layer.addTo(spotLayer);
        activeSpots.set(data.seq, { layer, ts, expiry, data });
    }
}

export function addSpot(spot) {
    if (activeSpots.has(spot.seq)) return;
    const layer = makeLayer(spot);
    if (!layer) return;
    layer.addTo(spotLayer);
    activeSpots.set(spot.seq, {
        layer,
        ts: spot.ts,
        expiry: spot.ts + displayMaxAgeSec,
        data: spot,
    });
}

export function addSpotBatch(spots) {
    for (const s of spots) addSpot(s);
    pruneOldSpots();
}

export function pruneOldSpots() {
    const now = Math.floor(Date.now() / 1000);
    for (const [seq, entry] of activeSpots) {
        if (now > entry.expiry) {
            spotLayer.removeLayer(entry.layer);
            activeSpots.delete(seq);
        }
    }
}

setInterval(pruneOldSpots, 30_000);

/*
 * When the user pans more than 60° from where spots were last drawn,
 * re-normalize all longitudes to the new center and redraw.
 * This handles panning from the Atlantic view to the Pacific view and back.
 */
let _lastRedrawLon = map.getCenter().lng;
map.on("moveend", () => {
    const lon = map.getCenter().lng;
    if (Math.abs(lon - _lastRedrawLon) > 60) {
        _lastRedrawLon = lon;
        redrawAll();
    }
});
