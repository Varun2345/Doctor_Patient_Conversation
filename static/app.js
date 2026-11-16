/* Consultation Recorder - single-page client (no build step).
 *
 * Recording reliability design:
 *  - One MediaRecorder runs from Start to Stop. It emits a small part every few
 *    seconds; parts are byte pieces of ONE continuous recording.
 *  - Every part is first written to this device (IndexedDB), then uploaded in
 *    order with unlimited retries. Uploading never pauses the recorder.
 *  - On Stop, the server joins the parts into one original file and verifies it.
 *    "Saved" is shown only after the server confirms. Otherwise the doctor sees
 *    exactly what is missing and can retry or download the on-device copy.
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

/* ------------------------------------------------------------------ DOM helper */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/* ------------------------------------------------------------------ formatting */
const pad2 = (n) => String(n).padStart(2, "0");
function clock(sec) {
  sec = Math.max(0, Math.floor(sec));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  return hh ? `${hh}:${pad2(mm)}:${pad2(ss)}` : `${pad2(mm)}:${pad2(ss)}`;
}
function humanDuration(sec) {
  if (sec === null || sec === undefined) return "";
  sec = Math.round(sec);
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return s ? `${m} min ${s} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const fmtDate = (iso) => new Date(iso).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const fmtDateTime = (iso) => `${fmtDate(iso)}, ${fmtTime(iso)}`;
function dayLabel(iso) {
  const d = new Date(iso), today = new Date();
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return fmtDate(iso);
}
function bytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function patientName(p) {
  if (!p) return null;
  return p.fullName || (p.hospitalId ? `ID ${p.hospitalId}` : p.patientId);
}
function patientLabel(p) {
  const name = patientName(p);
  return name ? h("span", {}, name) : h("span", { class: "unnamed" }, "Patient not entered");
}
function patientDetails(p) {
  if (!p) return "";
  const bits = [];
  if (p.age !== null && p.age !== undefined) bits.push(`${p.age} y`);
  if (p.gender) bits.push(p.gender);
  return bits.join(" · ");
}
const cap = (t) => t ? t[0].toUpperCase() + t.slice(1) : t;

/* ------------------------------------------------------------------ icons + avatars */
const ICONS = {
  moon: ["M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13z"],
  sun: ["M16 12a4 4 0 1 0-8 0a4 4 0 0 0 8 0", "M12 2v2", "M12 20v2", "M2 12h2", "M20 12h2", "M5 5l1.5 1.5", "M17.5 17.5L19 19", "M5 19l1.5-1.5", "M17.5 6.5L19 5"],
  mic: ["M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z", "M19 11a7 7 0 0 1-14 0", "M12 18v3", "M8.5 21h7"],
  plus: ["M12 5v14", "M5 12h14"],
  search: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "M20 20l-3.6-3.6"],
  clock: ["M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z", "M12 7.5V12l3 2"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  alert: ["M12 3.5l9 16H3z", "M12 10v4.5", "M12 17.4v.1"],
  download: ["M12 4v11", "M7.5 10.5L12 15l4.5-4.5", "M5 20h14"],
  back: ["M15 6l-6 6 6 6"],
  chevron: ["M9 6l6 6-6 6"],
  logout: ["M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4", "M10 16l-4-4 4-4", "M6 12h10"],
  file: ["M7 3h7l5 5v13H7z", "M14 3v5h5", "M10 13h6", "M10 17h6"],
  wave: ["M3 12h2", "M7 9v6", "M11 5v14", "M15 8v8", "M19 11v2"],
  shield: ["M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z", "M9 12l2 2 4-4"],
  user: ["M12 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8z", "M4 21a8 8 0 0 1 16 0"],
  users: ["M9 5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z", "M2.5 20a6.5 6.5 0 0 1 13 0", "M16 5.3a3.5 3.5 0 0 1 0 6.4", "M18 14.5a6.5 6.5 0 0 1 3.5 5.5"],
  calendar: ["M4 6h16v15H4z", "M4 10h16", "M8 3v4", "M16 3v4"],
  edit: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
  info: ["M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z", "M12 11v5", "M12 7.6v.1"],
  history: ["M3 12a9 9 0 1 0 3-6.7", "M3 4v5h5", "M12 8v4l3 2"],
  stop: ["M7 7h10v10H7z"],
};
const SVGNS = "http://www.w3.org/2000/svg";
function icon(name, cls = "") {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", `ic ${cls}`.trim());
  for (const d of ICONS[name] || []) {
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}
function initials(name) {
  const clean = (name || "").replace(/^(dr|mr|mrs|ms|prof)\.?\s+/i, "").trim();
  const parts = clean.split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
function hue(name) {
  let n = 0;
  for (const ch of name || "") n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  return `av-${n % 5}`;
}
function avatar(name, size = "") {
  if (!name) return h("span", { class: `avatar empty ${size}`.trim(), "aria-hidden": "true" }, icon("user"));
  return h("span", { class: `avatar ${hue(name)} ${size}`.trim(), "aria-hidden": "true" }, initials(name));
}
function dateBadge(iso) {
  const d = new Date(iso);
  return h("span", { class: "avatar date", "aria-hidden": "true" },
    h("b", {}, String(d.getDate())), h("small", {}, d.toLocaleDateString([], { month: "short" })));
}
function greeting() {
  const hr = new Date().getHours();
  return hr < 12 ? "Good morning" : hr < 17 ? "Good afternoon" : "Good evening";
}
function listJoin(names) {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
const backLink = (href, label) => h("a", { class: "back", href }, icon("back"), label);

/* ------------------------------------------------------------------ API */
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
async function api(path, { method = "GET", body, raw, timeout = 30000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  const opts = { method, credentials: "same-origin", signal: ctrl.signal, headers: {} };
  if (raw !== undefined) {
    opts.body = raw;
    opts.headers["Content-Type"] = "application/octet-stream";
  } else if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    throw new ApiError(0, err.name === "AbortError" ? "The server did not respond in time" : "Cannot reach the server");
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    let msg = (data && data.detail) || res.statusText || "Request failed";
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    if (res.status === 401 && !path.startsWith("/api/auth/") && !state.recorder) {
      state.me = null;
      go("#/login");
    }
    throw new ApiError(res.status, msg);
  }
  return data;
}

/* ------------------------------------------------------------------ on-device backup (IndexedDB) */
const IDB = {
  db: null,
  open() {
    if (this.db) return Promise.resolve(this.db);
    return new Promise((resolve, reject) => {
      if (!("indexedDB" in window)) return reject(new Error("IndexedDB unavailable"));
      const req = indexedDB.open("consult-recorder", 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        db.createObjectStore("recordings", { keyPath: "cid" });
        db.createObjectStore("chunks", { keyPath: ["cid", "seq"] });
      };
      req.onsuccess = () => { this.db = req.result; resolve(this.db); };
      req.onerror = () => reject(req.error);
    });
  },
  async tx(stores, mode, fn) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const t = db.transaction(stores, mode);
      let result;
      t.oncomplete = () => resolve(result);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error || new Error("IndexedDB aborted"));
      result = fn(t);
    });
  },
  putRec(rec) { return this.tx(["recordings"], "readwrite", (t) => { t.objectStore("recordings").put(rec); }); },
  putChunk(cid, seq, blob) { return this.tx(["chunks"], "readwrite", (t) => { t.objectStore("chunks").put({ cid, seq, blob }); }); },
  async allRecs() {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const req = db.transaction("recordings").objectStore("recordings").getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  },
  async chunks(cid) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const range = IDBKeyRange.bound([cid, 0], [cid, Number.MAX_SAFE_INTEGER]);
      const req = db.transaction("chunks").objectStore("chunks").getAll(range);
      req.onsuccess = () => resolve((req.result || []).sort((a, b) => a.seq - b.seq));
      req.onerror = () => reject(req.error);
    });
  },
  remove(cid) {
    return this.tx(["recordings", "chunks"], "readwrite", (t) => {
      t.objectStore("recordings").delete(cid);
      t.objectStore("chunks").delete(IDBKeyRange.bound([cid, 0], [cid, Number.MAX_SAFE_INTEGER]));
    });
  },
};

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
const extFor = (mime) => (mime || "").includes("mp4") ? "m4a" : (mime || "").includes("ogg") ? "ogg" : "webm";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ recorder engine */
const MIME_PREFS = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"];

function micErrorMessage(err) {
  const name = err && err.name;
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Microphone permission was denied. Allow microphone access for this site in the browser settings, then try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "No microphone was found. Connect a microphone and try again.";
  if (name === "NotReadableError" || name === "AbortError")
    return "The microphone is in use by another application or could not be started. Close other apps using it and try again.";
  if (!window.isSecureContext)
    return "Microphone access needs a secure connection (HTTPS, or localhost).";
  return `The microphone could not be started (${name || "unknown error"}).`;
}

class Recorder {
  constructor(onChange) {
    this.onChange = onChange;
    this.phase = "idle";        // idle | starting | recording | saving | saved | failed
    this.cid = null;
    this.seq = 0;               // parts produced
    this.pending = [];          // part numbers not yet confirmed by the server
    this.blobs = new Map();     // seq -> Blob (memory copy, for backup download)
    this.net = "ok";            // ok | retrying | auth | storage
    this.localBackup = true;
    this.endReason = "stopped";
    this.failure = null;
    this.level = 0;
    this.silentSince = null;
  }
  emit() {
    if (this.phase === "failed" && this.releaseLock) this.releaseLock();
    this.onChange(this);
  }

  get elapsed() {
    if (!this.t0) return 0;
    return ((this.t1 || performance.now()) - this.t0) / 1000;
  }

  async start({ patientId, consentConfirmed }) {
    const cfg = state.config.capture;
    this.phase = "starting"; this.emit();
    const raw = cfg.processing !== "browser";
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: { ideal: 1 },
          sampleRate: { ideal: 48000 },
          echoCancellation: !raw,
          noiseSuppression: !raw,
          autoGainControl: !raw,
        },
      });
    } catch (err) {
      this.phase = "idle";
      throw new Error(micErrorMessage(err));
    }
    const mimeType = MIME_PREFS.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m));
    if (!mimeType) {
      this.stopStream();
      this.phase = "idle";
      throw new Error("This browser cannot record audio. Use a current Chrome, Edge or Firefox.");
    }
    const track = this.stream.getAudioTracks()[0];
    const s = track.getSettings ? track.getSettings() : {};
    let created;
    try {
      created = await api("/api/consultations", {
        method: "POST",
        body: {
          patientId: patientId || null,
          consentConfirmed: consentConfirmed ?? null,
          capture: {
            processing: cfg.processing, mimeType, bitrate: cfg.bitrate,
            sampleRate: s.sampleRate ?? null, channelCount: s.channelCount ?? null,
            echoCancellation: s.echoCancellation ?? null, noiseSuppression: s.noiseSuppression ?? null,
            autoGainControl: s.autoGainControl ?? null,
            deviceLabel: (track.label || "").slice(0, 200), userAgent: navigator.userAgent.slice(0, 400),
          },
        },
      });
    } catch (err) {
      this.stopStream();
      this.phase = "idle";
      throw new Error(`Recording not started: ${err.message}. Nothing was recorded.`);
    }
    this.consultation = created.consultation;
    this.cid = created.consultation.consultationId;
    this.mimeType = mimeType;
    // Hold a browser lock while this tab owns the recording. If the tab closes or
    // crashes the lock disappears, which lets Home offer recovery immediately.
    if (navigator.locks) {
      navigator.locks.request(`rec-${this.cid}`, () => new Promise((resolve) => { this.releaseLock = resolve; })).catch(() => {});
    }
    try {
      await IDB.putRec({ cid: this.cid, mimeType, startedAt: this.consultation.startedAt,
        state: "recording", totalChunks: null, durationSec: null, heartbeat: Date.now() });
    } catch (_) {
      this.localBackup = false;
    }

    this.mr = new MediaRecorder(this.stream, { mimeType, audioBitsPerSecond: cfg.bitrate });
    this.mr.ondataavailable = (e) => this.onPart(e.data);
    this.mr.onerror = (e) => this.interrupt(`recorder error: ${(e.error && e.error.name) || "unknown"}`);
    this.mr.onstop = () => this.afterStop();
    track.onended = () => this.interrupt("microphone disconnected");
    this.mr.start(cfg.chunkSeconds * 1000);
    this.t0 = performance.now();
    this.phase = "recording";
    this.startMeter();
    this.keepAwake();
    this.tick = setInterval(() => this.emit(), 500);
    this.emit();
  }

  async onPart(blob) {
    if (!blob || blob.size === 0) return;
    const seq = this.seq++;
    this.blobs.set(seq, blob);
    this.pending.push(seq);
    this.pending.sort((a, b) => a - b);
    this.pump();
    if (this.localBackup) {
      try {
        await IDB.putChunk(this.cid, seq, blob);
        IDB.putRec({ cid: this.cid, mimeType: this.mimeType, startedAt: this.consultation.startedAt,
          state: this.phase === "recording" ? "recording" : "stopped", totalChunks: null,
          durationSec: this.elapsed, heartbeat: Date.now() }).catch(() => {});
      } catch (_) {
        this.localBackup = false;
      }
    }
  }

  async pump() {
    if (this.pumping) return;
    this.pumping = true;
    let backoff = 1000;
    try {
      while (this.pending.length) {
        const seq = this.pending[0];
        try {
          await api(`/api/consultations/${this.cid}/chunks/${seq}`, { method: "PUT", raw: this.blobs.get(seq), timeout: 45000 });
          this.pending.shift();
          backoff = 1000;
          if (this.net !== "ok") { this.net = "ok"; this.emit(); }
        } catch (err) {
          if (err.status === 409) { this.pending.shift(); continue; } // already finalized on server
          this.net = err.status === 401 ? "auth" : err.status === 507 ? "storage" : "retrying";
          this.lastNetError = err.message;
          this.emit();
          await sleep(backoff);
          backoff = Math.min(backoff * 2, 15000);
        }
      }
    } finally {
      this.pumping = false;
    }
  }

  stop() {
    if (this.phase !== "recording") return;
    this.endReason = "stopped";
    this.t1 = performance.now();
    this.phase = "saving";
    this.emit();
    try { this.mr.stop(); } catch (_) { this.afterStop(); }
  }

  interrupt(reason) {
    if (this.phase !== "recording") return;
    this.endReason = "interrupted";
    this.failure = reason;
    this.t1 = performance.now();
    this.phase = "saving";
    this.emit();
    api(`/api/consultations/${this.cid}/events`, { method: "POST", body: { type: "interrupted", detail: reason } }).catch(() => {});
    try {
      if (this.mr.state !== "inactive") this.mr.stop(); else this.afterStop();
    } catch (_) { this.afterStop(); }
  }

  stopStream() {
    if (this.stream) this.stream.getTracks().forEach((t) => { t.onended = null; t.stop(); });
  }

  async afterStop() {
    if (this.stopped) return;
    this.stopped = true;
    if (!this.t1) this.t1 = performance.now();
    clearInterval(this.tick);
    this.stopMeter();
    this.stopStream();
    this.releaseAwake();
    await sleep(50); // let the final dataavailable handler run
    this.totalChunks = this.seq;
    if (this.localBackup) {
      IDB.putRec({ cid: this.cid, mimeType: this.mimeType, startedAt: this.consultation.startedAt,
        state: "stopped", totalChunks: this.totalChunks, durationSec: this.elapsed, heartbeat: Date.now() }).catch(() => {});
    }
    await this.save();
  }

  async save() {
    this.phase = "saving"; this.failure = this.endReason === "interrupted" ? this.failure : null;
    this.abortSave = false; this.emit();
    while (this.pending.length) {
      this.pump();
      await sleep(400);
      if (this.abortSave) {
        this.phase = "failed";
        this.saveError = `Upload paused: ${this.pending.length} part(s) have not reached the server yet.`;
        this.emit();
        return;
      }
      this.emit();
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      let res;
      try {
        res = await api(`/api/consultations/${this.cid}/finalize`, {
          method: "POST", timeout: 180000,
          body: { totalChunks: this.totalChunks, durationSec: this.elapsed, reason: this.endReason,
                  detail: this.endReason === "interrupted" ? this.failure : null },
        });
      } catch (err) {
        this.phase = "failed";
        this.saveError = `The server could not save the recording: ${err.message}.`;
        this.emit();
        return;
      }
      this.result = res.consultation;
      if (res.consultation.status === "saved") {
        this.phase = "saved";
        if (this.releaseLock) this.releaseLock();
        IDB.remove(this.cid).catch(() => {});
        this.emit();
        return;
      }
      if (res.consultation.status === "incomplete" && attempt === 0) {
        this.pending = res.consultation.missingChunks.filter((s) => this.blobs.has(s));
        while (this.pending.length) { this.pump(); await sleep(400); }
        continue;
      }
      this.phase = "failed";
      this.saveError = res.consultation.status === "failed"
        ? "No audio was captured."
        : `The server is missing ${res.consultation.missingChunks.length} part(s) of the recording.`;
      this.emit();
      return;
    }
  }

  backupBlob() {
    const parts = [...this.blobs.keys()].sort((a, b) => a - b).map((k) => this.blobs.get(k));
    return new Blob(parts, { type: this.mimeType });
  }

  startMeter() {
    try {
      this.ac = new (window.AudioContext || window.webkitAudioContext)();
      const src = this.ac.createMediaStreamSource(this.stream);
      const an = this.ac.createAnalyser();
      an.fftSize = 2048;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      this.meterTimer = setInterval(() => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        const rms = Math.sqrt(sum / buf.length);
        this.level = Math.min(1, Math.sqrt(rms) * 2.2);
        // Digital silence (all zeros) means a muted / dead microphone, not a quiet room.
        if (rms < 0.00003) this.silentSince = this.silentSince || performance.now();
        else this.silentSince = null;
        const orb = $("#rec-orb");
        if (orb) orb.style.setProperty("--lvl", this.level.toFixed(3));
      }, 120);
    } catch (_) { /* meter is optional */ }
  }
  stopMeter() {
    clearInterval(this.meterTimer);
    if (this.ac) this.ac.close().catch(() => {});
  }
  get micSilent() {
    return this.silentSince && performance.now() - this.silentSince > 15000;
  }

  async keepAwake() {
    try {
      if ("wakeLock" in navigator) this.wake = await navigator.wakeLock.request("screen");
    } catch (_) { /* not supported / denied: recording still works */ }
  }
  releaseAwake() {
    if (this.wake) this.wake.release().catch(() => {});
    this.wake = null;
  }
}

document.addEventListener("visibilitychange", () => {
  const r = state.recorder;
  if (r && r.phase === "recording" && document.visibilityState === "visible" && !r.wake) r.keepAwake();
});

/* ------------------------------------------------------------------ app state + router */
const state = { config: null, me: null, recorder: null };

function recorderBusy() {
  const r = state.recorder;
  return r && ["starting", "recording", "saving"].includes(r.phase);
}

window.addEventListener("beforeunload", (e) => {
  if (recorderBusy() || (state.recorder && state.recorder.phase === "failed")) {
    e.preventDefault();
    e.returnValue = "";
  }
});

let currentHash = null;
function go(hash) {
  if (location.hash === hash) route(); else location.hash = hash;
}
window.addEventListener("hashchange", () => {
  if (recorderBusy() && currentHash && location.hash !== currentHash) {
    history.replaceState(null, "", currentHash);
    toast("A recording is in progress. Stop it before leaving this screen.");
    return;
  }
  route();
});

function toast(msg, ms = 4000) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, ms);
}

function mount(...nodes) {
  const view = $("#view");
  view.replaceChildren(...nodes.filter((n) => n !== null && n !== undefined && n !== false));
  window.scrollTo(0, 0);
}

let pollTimer = null;
async function route() {
  clearTimeout(pollTimer);
  currentHash = location.hash || "#/";
  const [path, query] = currentHash.slice(1).split("?");
  const params = new URLSearchParams(query || "");
  const parts = path.split("/").filter(Boolean);

  if (parts[0] === "login") return viewLogin();
  if (parts[0] === "signup") return viewSignup();
  if (!state.me) {
    try {
      state.me = (await api("/api/me")).doctor;
    } catch (err) {
      if (err.status === 401) return go("#/login");
      return mount(h("div", { class: "notice err" }, h("strong", {}, "Cannot reach the server"), err.message,
        h("div", { class: "btn-row" }, h("button", { class: "btn", onclick: route }, "Try again"))));
    }
  }
  renderChrome();
  if (state.recorder && !recorderBusy() && parts[0] !== "new") state.recorder = null;
  if (parts[0] === "new") return viewRecorder(params.get("patient"));
  if (parts[0] === "c" && parts[1]) return viewConsultation(parts[1]);
  if (parts[0] === "p" && parts[1]) return viewPatient(parts[1]);
  return viewHome();
}

/* ------------------------------------------------------------------ chrome */
function themeToggle() {
  const button = h("button", { class: "linkbtn theme-toggle", type: "button" });
  function update() {
    const dark = document.documentElement.dataset.theme === "dark";
    const label = `Switch to ${dark ? "light" : "dark"} theme`;
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.setAttribute("aria-pressed", String(dark));
    button.replaceChildren(icon(dark ? "sun" : "moon"));
  }
  button.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("consult-recorder-theme", theme); } catch (_) { /* Theme still works without storage. */ }
    update();
  });
  update();
  return button;
}

function brandBlock() {
  const c = state.config;
  const logo = c.logoUrl
    ? h("img", { class: "logo", src: c.logoUrl, alt: `${c.institutionName} logo` })
    : h("span", { class: "logo-placeholder" }, c.institutionName.slice(0, 4));
  return h("a", { class: "brand", href: "#/" }, h("span", { class: "logo-box" }, logo),
    h("span", { class: "brand-text" },
      h("span", { class: "brand-title" }, c.appTitle),
      h("span", { class: "brand-sub" }, c.institutionName)));
}

function renderChrome(showAccount = true) {
  const bar = $("#topbar");
  const busy = recorderBusy();
  bar.replaceChildren(h("div", { class: "topbar-inner" },
    brandBlock(),
    h("span", { class: "spacer" }),
    themeToggle(),
    showAccount && state.me ? h("div", { class: "who" }, avatar(state.me.fullName, "sm"),
      h("span", { class: "who-text" }, h("span", { class: "who-name" }, state.me.fullName), h("small", {}, state.me.email))) : null,
    showAccount && state.me ? h("button", { class: "linkbtn signout", disabled: busy, title: busy ? "Stop the recording first" : "Sign out", onclick: logout },
      icon("logout"), h("span", {}, "Sign out")) : null));
  bar.hidden = false;
  renderFooter();
}

function renderFooter() {
  const c = state.config;
  const foot = $("#footer");
  const team = c.teamNames || [];
  foot.replaceChildren(h("div", { class: "footer-inner" },
    h("div", { class: "footer-text" },
      team.length ? h("span", { class: "team" }, `Developed by ${listJoin(team)}`) : null,
      team.length && c.piName ? h("span", { class: "sep", "aria-hidden": "true" }, "·") : null,
      c.piName ? h("span", {}, `PI: ${c.piName}`) : null)));
  foot.hidden = false;
}

/* Two-column card used by sign-in and sign-up. */
function authShell(...children) {
  renderChrome(false);
  const c = state.config;
  return h("div", { class: "auth" },
    h("aside", { class: "auth-aside" },
      h("div", { class: "auth-aside-inner" },
        h("div", { class: "auth-mark" }, icon("mic")),
        h("h2", {}, c.appTitle),
        h("p", {}, "Record the whole consultation with one tap, then review it after the visit."),
        h("ul", { class: "auth-points" },
          h("li", {}, icon("mic"), "One Start, one Stop – recording continues through pauses"),
          h("li", {}, icon("shield"), "The original audio is stored unchanged"),
          c.processing && c.processing.transcription !== "none"
            ? h("li", {}, icon("file"), "Transcript with speaker labels after the visit") : null))),
    h("div", { class: "auth-main" }, h("div", { class: "signin" }, brandBlock(), ...children)));
}

async function logout() {
  if (recorderBusy()) return;
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  state.me = null;
  go("#/login");
}

/* ------------------------------------------------------------------ sign-in */
function viewLogin() {
  const err = h("div", { class: "notice err", hidden: true, role: "alert" });
  const email = h("input", { type: "email", name: "email", autocomplete: "username", required: true });
  const pw = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true });
  const btn = h("button", { class: "btn primary", type: "submit" }, "Sign in");
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      btn.disabled = true; err.hidden = true;
      try {
        state.me = (await api("/api/auth/login", { method: "POST", body: { email: email.value, password: pw.value } })).doctor;
        go("#/");
      } catch (ex) {
        err.textContent = ex.message; err.hidden = false;
      } finally {
        btn.disabled = false;
      }
    },
  },
  err,
  h("label", { class: "field" }, h("span", {}, "Email"), email),
  h("label", { class: "field" }, h("span", {}, "Password"), pw),
  btn);
  mount(authShell(h("h1", {}, "Doctor sign-in"), form,
    state.config.signup.enabled
      ? h("p", { class: "muted small switch" }, "New to the pilot? ", h("a", { href: "#/signup" }, "Create a doctor account"))
      : h("p", { class: "muted small" }, "Accounts are issued by the pilot administrator.")));
  email.focus();
}

/* ------------------------------------------------------------------ sign-up */
function viewSignup() {
  const cfg = state.config.signup;
  if (!cfg.enabled) {
    return mount(authShell(h("h1", {}, "Create an account"),
      h("p", {}, "Sign-up is turned off on this server. Ask the pilot administrator for an account."),
      h("p", { class: "small" }, h("a", { href: "#/login" }, "Back to sign-in"))));
  }
  const err = h("div", { class: "notice err", hidden: true, role: "alert" });
  const name = h("input", { type: "text", name: "fullName", autocomplete: "name", required: true, maxlength: 120, placeholder: "e.g. Dr. A. Kumar" });
  const email = h("input", { type: "email", name: "email", autocomplete: "username", required: true, maxlength: 200 });
  const pw = h("input", { type: "password", name: "password", autocomplete: "new-password", required: true, maxlength: 200 });
  const pw2 = h("input", { type: "password", name: "password2", autocomplete: "new-password", required: true, maxlength: 200 });
  const code = cfg.codeRequired ? h("input", { type: "text", name: "signupCode", autocomplete: "off", required: true, maxlength: 200 }) : null;
  const btn = h("button", { class: "btn primary", type: "submit" }, "Create account");
  const show = (msg) => { err.textContent = msg; err.hidden = false; err.scrollIntoView({ block: "nearest" }); };
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.hidden = true;
      if (pw.value !== pw2.value) return show("The passwords do not match.");
      btn.disabled = true;
      try {
        state.me = (await api("/api/auth/signup", { method: "POST", body: {
          fullName: name.value, email: email.value, password: pw.value,
          signupCode: code ? code.value : null,
        } })).doctor;
        go("#/");
        toast(`Welcome, ${state.me.fullName}. Your account is ready.`);
      } catch (ex) {
        show(ex.message);
      } finally {
        btn.disabled = false;
      }
    },
  },
  err,
  h("label", { class: "field" }, h("span", {}, "Full name"), name),
  h("label", { class: "field" }, h("span", {}, "Email"), email),
  h("label", { class: "field" }, h("span", {}, "Password"), pw),
  h("label", { class: "field" }, h("span", {}, "Confirm password"), pw2),
  code ? h("label", { class: "field" }, h("span", {}, "Sign-up code (from the pilot administrator)"), code) : null,
  btn);
  mount(authShell(h("h1", {}, "Create a doctor account"), form,
    h("p", { class: "muted small switch" }, "Already have an account? ", h("a", { href: "#/login" }, "Sign in"))));
  name.focus();
}

/* ------------------------------------------------------------------ status tags */
function statusTags(c) {
  const tags = [];
  const s = c.status;
  if (s === "saved") {
    const t = c.transcript.status;
    if (t === "done") tags.push(h("span", { class: "tag plain" }, icon("file"), "Transcript"));
    else if (t === "queued" || t === "running") tags.push(h("span", { class: "tag plain" }, "Transcribing…"));
    else if (t === "failed") tags.push(h("span", { class: "tag warn" }, "Transcript failed"));
    tags.push(h("span", { class: "tag ok" }, icon("check"), "Saved"));
  } else if (s === "recording") tags.push(h("span", { class: "tag warn" }, "Recording now"));
  else if (s === "interrupted") tags.push(h("span", { class: "tag warn" }, icon("alert"), "Interrupted – not saved"));
  else if (s === "incomplete") tags.push(h("span", { class: "tag err" }, icon("alert"), "Incomplete"));
  else if (s === "failed") tags.push(h("span", { class: "tag err" }, "No audio"));
  return h("span", { class: "tags" }, tags);
}

function consultationRow(c, { showDate = false } = {}) {
  const name = patientName(c.patient);
  const when = showDate ? `${fmtDate(c.startedAt)} · ${fmtTime(c.startedAt)}` : fmtTime(c.startedAt);
  return h("li", {}, h("a", { class: "row", href: `#/c/${c.consultationId}` },
    avatar(name),
    h("span", { class: "p" }, patientLabel(c.patient),
      h("span", { class: "sub" }, when, c.durationSec != null ? ` · ${humanDuration(c.durationSec)}` : "")),
    statusTags(c),
    icon("chevron", "chev")));
}

function emptyState(iconName, text) {
  return h("div", { class: "empty-state" }, icon(iconName), h("span", {}, text));
}

/* ------------------------------------------------------------------ home */
const FILTERS = [["today", "Today"], ["7d", "Last 7 days"], ["30d", "Last 30 days"], ["all", "All"]];
function sinceFor(f) {
  const d = new Date();
  if (f === "today") { d.setHours(0, 0, 0, 0); return d; }
  if (f === "7d") { d.setDate(d.getDate() - 7); return d; }
  if (f === "30d") { d.setDate(d.getDate() - 30); return d; }
  return null;
}
function getPref(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } }
function setPref(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* optional */ } }

async function viewHome() {
  const recovery = h("div");
  const attention = h("section", { class: "section card attention", hidden: true });
  const historyList = h("div");
  const more = h("button", { class: "btn", hidden: true }, "Show earlier consultations");
  const search = h("input", { type: "search", placeholder: "Search patients by name", "aria-label": "Search patients", autocomplete: "off" });
  const results = h("div", { class: "search-results", hidden: true, role: "listbox" });
  let filter = getPref("homeFilter", "7d");
  if (!FILTERS.some(([k]) => k === filter)) filter = "7d";
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Show consultations from" });
  let all = [];

  mount(
    h("div", { class: "topline" },
      h("div", { class: "searchbox big-search" }, icon("search"), search, results),
      h("button", { class: "btn primary add-patient", type: "button", onclick: () => openAddPatient() }, icon("plus"), "Add patient")),
    recovery,
    h("section", { class: "hero" },
      h("div", { class: "hero-text" },
        h("h2", {}, "Start a new consultation"),
        h("p", {}, "Choose or add the patient, then record the whole conversation with one tap.")),
      h("a", { class: "big new", href: "#/new" }, icon("mic"), "New consultation")),
    attention,
    h("section", { class: "section card" },
      h("div", { class: "section-head" }, h("h2", {}, icon("history"), "Recent consultations"), chips),
      historyList,
      h("div", { class: "more-row" }, more)),
  );

  checkRecovery(recovery, () => reload());

  // --- patient search (results drop down under the search bar)
  let seq = 0;
  async function runSearch() {
    const mine = ++seq;
    const q = search.value.trim();
    if (!q) { results.hidden = true; results.replaceChildren(); return; }
    try {
      const { patients } = await api(`/api/patients?limit=8&q=${encodeURIComponent(q)}`);
      if (mine !== seq) return;
      const rows = patients.map((p) => h("li", {}, h("a", { class: "row patient", href: `#/p/${p.patientId}` },
        avatar(patientName(p), "sm"),
        h("span", { class: "p" }, h("span", {}, patientName(p)),
          h("span", { class: "sub" }, [patientDetails(p), p.lastConsultationAt ? `last seen ${dayLabel(p.lastConsultationAt).toLowerCase()}` : ""].filter(Boolean).join(" · ") || "No details")),
        h("span", { class: "d" }, `${p.consultationCount} consultation${p.consultationCount === 1 ? "" : "s"}`),
        icon("chevron", "chev"))));
      results.replaceChildren(
        rows.length ? h("ul", { class: "list" }, rows) : h("div", { class: "empty-state" }, icon("search"), h("span", {}, `No patient named “${q}”.`)),
        h("button", { class: "linkbtn add-from-search", type: "button", onclick: () => openAddPatient(q) }, icon("plus"), `Add “${q}” as a new patient`));
      results.hidden = false;
    } catch (err) {
      results.replaceChildren(h("div", { class: "notice err" }, err.message));
      results.hidden = false;
    }
  }
  let debounce;
  search.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(runSearch, 180); });
  search.addEventListener("keydown", (e) => { if (e.key === "Escape") { search.value = ""; runSearch(); } });
  search.addEventListener("focus", () => { if (search.value.trim()) runSearch(); });
  document.addEventListener("click", function close(e) {
    if (!document.body.contains(results)) return document.removeEventListener("click", close);
    if (!e.target.closest(".big-search")) results.hidden = true;
  });

  // --- recent consultations with a time filter
  function drawChips() {
    chips.replaceChildren(...FILTERS.map(([key, label]) => h("button", {
      class: `chip${key === filter ? " on" : ""}`, type: "button", "aria-pressed": key === filter ? "true" : "false",
      onclick: () => { filter = key; setPref("homeFilter", key); drawChips(); reload(); },
    }, label)));
  }
  drawChips();

  function reload() { all = []; loadHistory(); }
  async function loadHistory(before) {
    more.disabled = true;
    const since = sinceFor(filter);
    const qs = [`limit=30`, before ? `before=${encodeURIComponent(before)}` : "", since ? `since=${encodeURIComponent(since.toISOString())}` : ""].filter(Boolean).join("&");
    try {
      const data = await api(`/api/consultations?${qs}`);
      all = all.concat(data.consultations);
      more.hidden = !data.hasMore;
      renderHistory();
    } catch (err) {
      historyList.replaceChildren(h("div", { class: "notice err" }, err.message));
    } finally {
      more.disabled = false;
    }
  }
  function renderHistory() {
    const needs = all.filter((c) => ["interrupted", "incomplete", "failed"].includes(c.status));
    attention.hidden = !needs.length;
    attention.replaceChildren(
      h("div", { class: "section-head" }, h("h2", {}, icon("alert"), "Needs attention")),
      h("ul", { class: "list" }, needs.map((c) => consultationRow(c, { showDate: true }))));
    const done = all.filter((c) => !needs.includes(c));
    if (!done.length) {
      const msg = { today: "No consultations today.", "7d": "No consultations in the last 7 days.", "30d": "No consultations in the last 30 days.", all: "No consultations recorded yet." }[filter];
      historyList.replaceChildren(emptyState("mic", msg));
      return;
    }
    const groups = [];
    for (const c of done) {
      const label = dayLabel(c.startedAt);
      if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, items: [] });
      groups[groups.length - 1].items.push(c);
    }
    historyList.replaceChildren(...groups.flatMap((g) => [h("div", { class: "day" }, g.label), h("ul", { class: "list" }, g.items.map((c) => consultationRow(c)))]));
  }
  more.addEventListener("click", () => loadHistory(all.length ? all[all.length - 1].startedAt : null));
  loadHistory();
}

/* Patient form fields (name required; age and sex optional). */
function patientFields(p = {}) {
  const name = h("input", { type: "text", name: "patientName", maxlength: 120, autocomplete: "off", required: true, placeholder: "Patient's name", value: p.fullName || "" });
  const age = h("input", { type: "number", name: "patientAge", min: 0, max: 130, inputmode: "numeric", value: p.age ?? "" });
  const gender = h("select", { name: "patientSex" }, ["", "female", "male", "other"].map((g) => h("option", { value: g, selected: (p.gender || "") === g }, g ? cap(g) : "—")));
  return {
    name,
    el: h("div", { class: "inline-fields" },
      h("label", { class: "field" }, h("span", {}, "Name"), name),
      h("label", { class: "field" }, h("span", {}, "Age"), age),
      h("label", { class: "field" }, h("span", {}, "Sex"), gender)),
    body: () => ({ fullName: name.value.trim() || null, age: age.value === "" ? null : Number(age.value), gender: gender.value || null }),
  };
}

/* "Add patient" dialog (home page). */
function openAddPatient(prefill = "") {
  const dlg = h("dialog", { class: "modal", "aria-label": "Add a patient" });
  const f = patientFields({ fullName: prefill });
  const err = h("div", { class: "notice err", hidden: true, role: "alert" });
  const close = () => { dlg.close(); dlg.remove(); };
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.hidden = true;
      try {
        const { patient } = await api("/api/patients", { method: "POST", body: f.body() });
        close();
        go(`#/p/${patient.patientId}`);
        toast(`${patientName(patient)} added`);
      } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    },
  },
  h("div", { class: "modal-head" }, h("h2", {}, icon("user"), "Add a patient"),
    h("button", { class: "linkbtn", type: "button", "aria-label": "Close", onclick: close }, "Close")),
  h("p", { class: "muted small" }, "Only the name is needed. You can start a consultation for them straight after."),
  err, f.el,
  h("div", { class: "btn-row" }, h("button", { class: "btn primary", type: "submit" }, icon("plus"), "Add patient"),
    h("button", { class: "btn", type: "button", onclick: close }, "Cancel")));
  dlg.append(form);
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  document.body.append(dlg);
  dlg.showModal();
  f.name.focus();
}

/* Recordings left on this device that never reached "saved" (crash, closed tab, network). */
async function checkRecovery(container, onRecovered) {
  let recs = [];
  try { recs = await IDB.allRecs(); } catch (_) { return; }
  const active = state.recorder && state.recorder.cid;
  let held = null;
  try {
    if (navigator.locks && navigator.locks.query) held = new Set((await navigator.locks.query()).held.map((l) => l.name));
  } catch (_) { held = null; }
  recs = recs.filter((r) => r.cid !== active && (held
    ? !held.has(`rec-${r.cid}`)
    : r.state !== "recording" || Date.now() - (r.heartbeat || 0) > 20000));
  for (const rec of recs) {
    let server = null;
    try { server = (await api(`/api/consultations/${rec.cid}`)).consultation; } catch (err) { if (err.status === 404) server = { status: "unknown" }; }
    if (server && server.status === "saved") { IDB.remove(rec.cid).catch(() => {}); continue; }
    const chunks = await IDB.chunks(rec.cid);
    if (!chunks.length) { IDB.remove(rec.cid).catch(() => {}); continue; }
    const msg = h("div", {}, `Recording started ${fmtDateTime(rec.startedAt)} (about ${humanDuration(rec.durationSec || chunks.length * state.config.capture.chunkSeconds)}) is stored on this device but was not fully saved to the server.`);
    const status = h("div", { class: "small", style: null });
    const uploadBtn = h("button", { class: "btn primary" }, "Upload and save now");
    const dlBtn = h("button", { class: "btn" }, "Download copy");
    const box = h("div", { class: "notice warn", role: "alert" }, h("strong", {}, "Unsaved recording on this device"), msg, status,
      h("div", { class: "btn-row" }, uploadBtn, dlBtn));
    container.append(box);
    dlBtn.addEventListener("click", () => downloadBlob(new Blob(chunks.map((c) => c.blob), { type: rec.mimeType }), `${rec.cid}-device-copy.${extFor(rec.mimeType)}`));
    uploadBtn.addEventListener("click", async () => {
      uploadBtn.disabled = true;
      try {
        for (const [i, c] of chunks.entries()) {
          status.textContent = `Uploading part ${i + 1} of ${chunks.length}…`;
          await api(`/api/consultations/${rec.cid}/chunks/${c.seq}`, { method: "PUT", raw: c.blob, timeout: 60000 })
            .catch((err) => { if (err.status !== 409) throw err; });
        }
        status.textContent = "Saving…";
        const res = await api(`/api/consultations/${rec.cid}/finalize`, { method: "POST", timeout: 180000,
          body: { totalChunks: rec.totalChunks ?? chunks.length, durationSec: rec.durationSec, reason: "recovered" } });
        if (res.consultation.status === "saved") {
          await IDB.remove(rec.cid);
          box.className = "notice ok";
          box.replaceChildren(h("strong", {}, "Recovered recording saved"), h("a", { href: `#/c/${rec.cid}` }, "Open consultation"));
          if (onRecovered) onRecovered();
        } else {
          status.textContent = `Server still missing ${res.consultation.missingChunks.length} part(s). Keep the device copy (Download copy).`;
          uploadBtn.disabled = false;
        }
      } catch (err) {
        status.textContent = `Not saved: ${err.message}. The copy is still on this device.`;
        uploadBtn.disabled = false;
      }
    });
  }
}

/* ------------------------------------------------------------------ patient picker */
function patientPicker({ onSelect, onCancel, heading }) {
  const results = h("ul", { class: "list" });
  const search = h("input", { type: "search", placeholder: "Search patients by name", "aria-label": "Search patients", autocomplete: "off" });
  const f = patientFields();
  const err = h("div", { class: "notice err", hidden: true });
  let autoName = "";
  let seq = 0;
  async function run() {
    const mine = ++seq;
    const q = search.value.trim();
    if (!f.name.value || f.name.value === autoName) { f.name.value = q; autoName = q; }
    try {
      const { patients } = await api(`/api/patients?limit=8&q=${encodeURIComponent(q)}`);
      if (mine !== seq) return;
      results.replaceChildren(...patients.map((p) => h("li", {}, h("button", { class: "row", type: "button", onclick: () => onSelect(p) },
        avatar(patientName(p), "sm"),
        h("span", { class: "p" }, h("span", {}, patientName(p)), h("span", { class: "sub" }, patientDetails(p) || "No details")),
        h("span", { class: "d" }, p.lastConsultationAt ? dayLabel(p.lastConsultationAt) : "")))));
      if (!patients.length) results.replaceChildren(h("li", { class: "muted small pad" }, q ? "No match – add them below." : "No patients yet – add one below."));
    } catch (e) { results.replaceChildren(h("li", { class: "notice err" }, e.message)); }
  }
  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(run, 180); });
  run();
  const add = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      err.hidden = true;
      try {
        const { patient } = await api("/api/patients", { method: "POST", body: f.body() });
        onSelect(patient);
      } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    },
  },
  h("h3", {}, "Or add a new patient"),
  err, f.el,
  h("div", { class: "btn-row" }, h("button", { class: "btn primary", type: "submit" }, "Add and select"),
    onCancel ? h("button", { class: "btn", type: "button", onclick: onCancel }, "Cancel") : null));
  const el = h("div", { class: "picker" }, heading ? h("div", { class: "picker-title" }, heading) : null,
    h("div", { class: "searchbox" }, icon("search"), search), results, add);
  setTimeout(() => search.focus(), 0);
  return el;
}

/* ------------------------------------------------------------------ recorder screen */
function ctxBox(iconName, label, value, ...extra) {
  return h("div", { class: "ctx" },
    h("div", { class: "ctx-label" }, icon(iconName), label),
    h("div", { class: "ctx-value" }, value), ...extra);
}

async function viewRecorder(patientId) {
  const cfg = state.config;
  let patient = null;
  if (state.recorder && state.recorder.phase !== "idle") return renderRecorder();
  if (patientId) {
    try { patient = (await api(`/api/patients/${patientId}`)).patient; } catch (_) { patient = null; }
  }
  let consent = cfg.consent.mode !== "confirm";
  let errorMsg = null;
  let picking = cfg.requirePatient && !patient;
  state.recorder = null;
  draw();

  function draw() {
    const needPatient = cfg.requirePatient && !patient;
    const context = h("div", { class: "rec-context two" },
      ctxBox("user", "Patient", needPatient ? h("span", { class: "unnamed" }, "Required – choose or add below") : patientLabel(patient),
        needPatient ? null : h("div", {},
          h("button", { class: "linkbtn", type: "button", onclick: () => { picking = !picking; draw(); } }, patient ? "Change" : "Add or choose (optional)"),
          patient && !cfg.requirePatient ? h("button", { class: "linkbtn", type: "button", onclick: () => { patient = null; draw(); } }, "Clear") : null)),
      ctxBox("shield", "Doctor", state.me.fullName));
    const picker = picking ? patientPicker({
      heading: needPatient ? "Who is the patient?" : "Change patient",
      onSelect: (p) => { patient = p; picking = false; draw(); },
      onCancel: needPatient ? null : () => { picking = false; draw(); },
    }) : null;
    const startBtn = h("button", { class: "big start", type: "button", disabled: !consent || needPatient }, icon("mic"), "Start recording");
    startBtn.addEventListener("click", async () => {
      startBtn.disabled = true;
      errorMsg = null;
      const rec = new Recorder(() => renderRecorder());
      rec.patient = patient;
      state.recorder = rec;
      try {
        await rec.start({ patientId: patient && patient.patientId, consentConfirmed: cfg.consent.mode === "off" ? null : (cfg.consent.mode === "confirm" ? consent : null) });
        renderChrome();
      } catch (err) {
        state.recorder = null;
        errorMsg = err.message;
        draw();
      }
    });
    let consentEl = null;
    if (cfg.consent.mode === "confirm") {
      const box = h("input", { type: "checkbox", checked: consent });
      box.addEventListener("change", () => { consent = box.checked; startBtn.disabled = !consent || needPatient; });
      consentEl = h("div", { class: "consent" }, h("label", {}, box, h("span", {}, cfg.consent.text)));
    } else if (cfg.consent.mode === "reminder") {
      consentEl = h("p", { class: "consent muted" }, cfg.consent.text);
    }
    mount(backLink("#/", "Home"), h("div", { class: "rec-shell" }, context, picker,
      errorMsg ? h("div", { class: "notice err", role: "alert", style: null }, h("strong", {}, "Recording did not start"), errorMsg) : null,
      h("div", { class: "rec-center" }, startBtn, consentEl,
        h("p", { class: "rec-hint" }, icon("info"), needPatient
          ? "Choose the patient or add a new one above to enable recording."
          : "One press records the whole consultation. It keeps going through pauses and speaker changes until you press Stop."))));
  }
}

function recContext(r) {
  return h("div", { class: "rec-context" },
    ctxBox("user", "Patient", patientLabel(r.patient)),
    ctxBox("shield", "Doctor", state.me.fullName),
    ctxBox("calendar", "Started", r.consultation ? `${fmtTime(r.consultation.startedAt)} · ${dayLabel(r.consultation.startedAt)}` : "…"));
}

function renderRecorder() {
  const r = state.recorder;
  if (!r || location.hash.split("?")[0] !== "#/new") return;

  if (r.phase === "starting") {
    return mountRec(r, h("div", { class: "rec-center" }, h("div", { class: "result busy" }, h("div", { class: "icon" }), h("p", {}, "Starting microphone…"))));
  }

  if (r.phase === "recording") {
    // Update in place when possible so the stop button never flickers.
    const existing = $("#rec-live");
    let saveText = "Saving continuously to the hospital server";
    let saveCls = "save-line";
    if (r.net === "retrying") { saveText = `Server not reachable – still recording; audio kept on this device and retrying${r.localBackup ? "" : " (memory only)"}`; saveCls += " warn"; }
    if (r.net === "auth") { saveText = "Signed out on the server – still recording; audio kept on this device. Sign in again after stopping to upload."; saveCls += " warn"; }
    if (r.net === "storage") { saveText = "Server storage problem – still recording; audio kept on this device and retrying"; saveCls += " err"; }
    if (r.micSilent) { saveText = "No sound is reaching the microphone. Check that it is connected and not muted."; saveCls = "save-line err"; }
    if (existing) {
      $("#rec-timer").textContent = clock(r.elapsed);
      const line = $("#save-line");
      line.textContent = saveText; line.className = saveCls;
      return;
    }
    const stopBtn = h("button", { class: "big stop", type: "button", onclick: () => r.stop() }, icon("stop", "fill"), "Stop & save");
    return mountRec(r, h("div", { class: "rec-center", id: "rec-live" },
      h("div", { class: "orb", id: "rec-orb", title: "Microphone level" },
        h("div", { class: "rec-status", role: "status" }, h("span", { class: "live", "aria-hidden": "true" }), "Recording"),
        h("div", { class: "timer", id: "rec-timer", "aria-label": "Elapsed time" }, clock(r.elapsed))),
      h("div", { class: saveCls, id: "save-line", "aria-live": "polite" }, saveText),
      stopBtn));
  }

  if (r.phase === "saving") {
    const left = r.pending.length;
    return mountRec(r, h("div", { class: "result busy" },
      h("div", { class: "icon", "aria-hidden": "true" }),
      h("h2", {}, "Saving recording…"),
      r.endReason === "interrupted" ? h("p", {}, `Recording stopped unexpectedly (${r.failure}). Saving the audio captured so far.`) : null,
      h("p", {}, `Length ${clock(r.elapsed)}.` + (left ? ` Uploading ${left} remaining part${left === 1 ? "" : "s"}…` : " Joining and verifying on the server…")),
      r.net !== "ok" && left ? h("p", { class: "save-line warn" }, `Upload problem: ${r.lastNetError || "retrying"}. Keep this page open – it will keep retrying.`) : null,
      left && r.net !== "ok" ? h("div", { class: "btn-row" },
        h("button", { class: "btn", onclick: () => downloadBlob(r.backupBlob(), `${r.cid}-device-copy.${extFor(r.mimeType)}`) }, icon("download"), "Download device copy"),
        r.localBackup ? h("button", { class: "btn", onclick: () => { r.abortSave = true; } }, "Finish later (copy stays on this device)") : null) : null));
  }

  if (r.phase === "saved") {
    const c = r.result;
    const tr = c.transcript.status;
    return mountRec(r, h("div", { class: "result ok" },
      h("div", { class: "icon", "aria-hidden": "true" }, icon("check")),
      h("h2", {}, "Consultation saved"),
      r.endReason === "interrupted" ? h("p", { class: "save-line warn" }, `The recording ended unexpectedly (${r.failure}). Everything captured up to that point is saved.`) : null,
      h("div", { class: "facts" },
        h("div", {}, "Length", h("b", {}, humanDuration(c.durationSec))),
        h("div", {}, "Size", h("b", {}, bytes(c.audio.original.sizeBytes))),
        h("div", {}, "Patient", h("b", {}, patientName(r.patient) || "Not entered"))),
      h("p", { class: "muted small" }, "The original audio is stored on the hospital server. " +
        (tr === "disabled" ? "Transcription is turned off on this server." : "A transcript will be prepared in the background – you don’t need to wait.")),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: () => { state.recorder = null; go("#/new"); } }, icon("mic"), "Start next consultation"),
        h("a", { class: "btn", href: `#/c/${c.consultationId}`, onclick: () => { state.recorder = null; } }, "Open this consultation"),
        h("a", { class: "btn", href: "#/", onclick: () => { state.recorder = null; } }, "Home"))));
  }

  if (r.phase === "failed") {
    return mountRec(r, h("div", { class: "result err", role: "alert" },
      h("div", { class: "icon", "aria-hidden": "true" }, icon("alert")),
      h("h2", {}, "Recording NOT fully saved"),
      h("p", {}, r.saveError || "The recording could not be saved."),
      h("p", {}, r.localBackup ? "A complete copy is kept on this device. Retry now, or later from the Home screen of this device. You can also download the copy for the pilot administrator." : "Download the copy from this page before closing it."),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: () => r.save() }, "Retry saving"),
        h("button", { class: "btn", onclick: () => downloadBlob(r.backupBlob(), `${r.cid}-device-copy.${extFor(r.mimeType)}`) }, icon("download"), "Download device copy"),
        h("a", { class: "btn", href: "#/", onclick: () => { state.recorder = null; } }, "Home"))));
  }
}

function mountRec(r, body) {
  const busy = recorderBusy();
  renderChrome();
  mount(busy ? h("span", { class: "back muted" }, icon("mic"), "Recording in progress – stay on this screen") : backLink("#/", "Home"),
    h("div", { class: "rec-shell" }, recContext(r), body));
}

/* ------------------------------------------------------------------ consultation detail */
async function viewConsultation(cid) {
  let c;
  try {
    c = (await api(`/api/consultations/${cid}`)).consultation;
  } catch (err) {
    return mount(backLink("#/", "Home"), h("div", { class: "notice err" }, err.message));
  }
  let picking = false;
  let editing = false;
  let player = null; // kept across redraws so playback is never interrupted
  const busy = (x) => ["queued", "running"].includes(x.transcript.status);
  draw();
  if (busy(c)) schedulePoll();

  /* Poll quietly; redraw the page only once, when transcript + speaker labels are done. */
  function schedulePoll() {
    pollTimer = setTimeout(async () => {
      if (location.hash !== `#/c/${cid}`) return;
      try {
        const fresh = (await api(`/api/consultations/${cid}`)).consultation;
        if (busy(fresh)) {
          c.transcript.stage = fresh.transcript.stage;
          const line = $("#tr-progress");
          if (line) line.textContent = progressText(fresh);
          return schedulePoll();
        }
        c = fresh;
        if (editing || picking) return;
        draw();
      } catch (_) { schedulePoll(); }
    }, 8000);
  }

  function progressText(x) {
    return x.transcript.stage === "speakers"
      ? "Identifying who spoke when (Speaker A, B…). The transcript appears here when it is ready."
      : "Preparing the transcript on the hospital server. It appears here when it is ready.";
  }

  function seek(t) {
    if (!player) return;
    player.currentTime = Math.max(0, t - 0.3);
    player.play().catch(() => {});
  }

  function draw() {
    const pname = patientName(c.patient);
    const back = backLink(c.patientId ? `#/p/${c.patientId}` : "#/", c.patientId ? "Patient history" : "Home");
    const header = h("header", { class: "ph" }, avatar(pname, "lg"),
      h("div", { class: "ph-text" },
        h("h1", { class: "page-title" }, pname || "Patient not entered"),
        h("p", { class: "page-sub" },
          h("span", {}, icon("calendar"), fmtDateTime(c.startedAt)),
          c.durationSec != null ? h("span", {}, icon("clock"), humanDuration(c.durationSec)) : null,
          h("span", {}, icon("shield"), c.doctorName || c.doctorId),
          c.patient ? null : h("button", { class: "linkbtn", type: "button", onclick: () => { picking = !picking; draw(); } }, "Assign patient"))),
      statusTags(c));
    const picker = picking ? patientPicker({
      heading: "Assign patient",
      onSelect: async (p) => {
        try { c = (await api(`/api/consultations/${cid}`, { method: "PATCH", body: { patientId: p.patientId } })).consultation; picking = false; draw(); toast("Patient assigned"); }
        catch (e) { toast(e.message); }
      },
      onCancel: () => { picking = false; draw(); },
    }) : null;

    // --- audio: the original only
    const audio = h("section", { class: "panel" }, h("h2", {}, h("span", { class: "ptitle" }, icon("wave"), "Audio recording")));
    const o = c.audio.original;
    if (c.status === "saved" && o && o.available) {
      const src = `/api/consultations/${cid}/audio/original`;
      if (!player) {
        player = h("audio", { controls: true, preload: "auto", src, id: "orig-audio" });
        fixDuration(player);
      }
      audio.append(h("div", { class: "audio-block" },
        player,
        h("div", { class: "audio-meta" },
          h("a", { class: "btn", href: `${src}?download=1` }, icon("download"), "Download"),
          h("span", { class: "muted small" }, bytes(o.sizeBytes)),
          o.verified === false ? h("span", { class: "tag err" }, "Integrity check failed – report to administrator") : null)));
      if (c.endReason === "interrupted") audio.append(h("p", { class: "muted small" }, "The recording ended unexpectedly; audio up to that point is saved."));
      if (c.endReason === "recovered") audio.append(h("p", { class: "muted small" }, "Recovered from the recording device after an upload problem."));
    } else {
      audio.append(statusNotice(c));
    }

    // --- transcript (one version: the latest saved one)
    const tr = c.transcript;
    const trTag = { queued: h("span", { class: "tag plain" }, "In progress"), running: h("span", { class: "tag plain" }, "In progress"), failed: h("span", { class: "tag err" }, "Failed") }[tr.status] || null;
    const transcript = h("section", { class: "panel" }, h("h2", {}, h("span", { class: "ptitle" }, icon("file"), "Transcript"), trTag));
    if (tr.status === "done") {
      const segs = tr.segments || [];
      if (editing) {
        transcript.append(transcriptEditor(segs, { onSeek: seek, onCancel: () => { editing = false; draw(); }, onSave: save }));
      } else {
        transcript.append(speakerSummary(segs), segmentList(segs, orderOf(segs), seek));
        if (c.diarization.status === "failed" && !tr.edited)
          transcript.append(h("p", { class: "muted small" }, "Speaker labels could not be added; the transcript is shown without them."));
        transcript.append(h("div", { class: "panel-foot" },
          h("p", { class: "muted small" },
            tr.edited && tr.editedAt ? `Last edited by ${tr.editedByName || "doctor"} on ${fmtDateTime(tr.editedAt)}. ` : "Prepared automatically – please check it. ",
            "Click a time to play the recording from there."),
          h("div", { class: "btn-row" },
            h("button", { class: "btn primary", onclick: () => { editing = true; draw(); } }, icon("edit"), "Edit transcript"))));
      }
    } else if (tr.status === "disabled") {
      transcript.append(h("p", { class: "muted" }, "Transcription is turned off on this server. The original recording is the record of this consultation."));
    } else if (tr.status === "failed") {
      transcript.append(h("div", { class: "notice warn" }, h("strong", {}, "Transcription failed"), "The recording is saved and unaffected. ", tr.error || ""),
        h("button", { class: "btn", onclick: retranscribe }, "Try transcription again"));
    } else if (c.status === "saved") {
      transcript.append(h("div", { class: "progress-line" }, h("span", { class: "spinner", "aria-hidden": "true" }),
        h("span", { id: "tr-progress" }, progressText(c))));
    } else {
      transcript.append(h("p", { class: "muted" }, "No transcript – the recording is not saved."));
    }

    mount(back, header, picker, audio, transcript);
  }

  async function save(segments) {
    try {
      c = (await api(`/api/consultations/${cid}/transcript`, { method: "PUT", body: { segments } })).consultation;
      editing = false; draw();
      toast("Transcript saved.");
    } catch (e) { toast(e.message); }
  }

  async function retranscribe() {
    try { c = (await api(`/api/consultations/${cid}/transcribe`, { method: "POST" })).consultation; draw(); schedulePoll(); }
    catch (e) { toast(e.message); }
  }

  function statusNotice(c) {
    if (c.status === "interrupted" || c.status === "recording") {
      const btn = h("button", { class: "btn primary" }, "Save the audio received so far");
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await api(`/api/consultations/${cid}/finalize`, { method: "POST", timeout: 180000, body: { totalChunks: null, reason: "server-recovery", durationSec: c.chunksReceived * state.config.capture.chunkSeconds } });
          c = (await api(`/api/consultations/${cid}`)).consultation; draw();
        } catch (e) { toast(e.message); btn.disabled = false; }
      });
      return h("div", { class: "notice warn" }, h("strong", {}, c.status === "recording" ? "Recording in progress or just interrupted" : "Recording interrupted – not saved yet"),
        `The server received ${c.chunksReceived} part(s) (about ${humanDuration(c.chunksReceived * state.config.capture.chunkSeconds)}). If the recording device is available, open this app on it first – it may hold the rest of the audio.`,
        h("div", { class: "btn-row" }, btn));
    }
    if (c.status === "incomplete")
      return h("div", { class: "notice err" }, h("strong", {}, "Incomplete recording"), `${c.missingChunks.length} part(s) never reached the server. Open this app on the recording device to upload its copy.`);
    if (c.status === "failed")
      return h("div", { class: "notice err" }, h("strong", {}, "No audio"), "No audio was captured for this consultation.");
    return h("p", { class: "muted" }, "Audio not available.");
  }
}

/* ------------------------------------------------------------------ transcript display */
function spkDot(name) {
  const short = name.replace(/^Speaker\s+/, "");
  return h("span", { class: "spk-dot", "aria-hidden": "true" }, h("span", {}, short.length <= 2 ? short : short[0].toUpperCase()));
}

function speakerOrder(d) {
  const order = [];
  for (const t of (d && d.turns) || []) if (!order.includes(t.speaker)) order.push(t.speaker);
  return order;
}
function orderOf(segs) {
  const order = [];
  for (const s of segs) if (s.speaker && !order.includes(s.speaker)) order.push(s.speaker);
  return order;
}

/* "2 speakers · Speaker A 0:41 · Speaker B 0:27", from the transcript lines. */
function speakerSummary(segs) {
  const order = orderOf(segs || []);
  if (!order.length) return null;
  const total = {};
  for (const sg of segs) if (sg.speaker) total[sg.speaker] = (total[sg.speaker] || 0) + Math.max(0, sg.end - sg.start);
  return h("div", { class: "speakers" },
    h("span", { class: "muted" }, `${order.length} speaker${order.length === 1 ? "" : "s"}`),
    order.map((name, i) => h("span", { class: `spk-pill spk-${i % 6}` }, spkDot(name), `${name} · ${clock(total[name])}`)));
}
const LANG_NAMES = { en: "English", hi: "Hindi", te: "Telugu", ta: "Tamil", kn: "Kannada", ml: "Malayalam", mr: "Marathi", bn: "Bengali", ur: "Urdu", gu: "Gujarati", pa: "Punjabi" };

function segmentList(segs, order, onSeek) {
  if (!segs.length) return h("p", { class: "muted" }, "No speech was detected.");
  const mixed = new Set(segs.map((s) => s.language).filter(Boolean)).size > 1;
  return h("ul", { class: "segments" }, segs.map((s, i) => {
    const same = i > 0 && segs[i - 1].speaker === s.speaker;
    const idx = Math.max(0, order.indexOf(s.speaker));
    return h("li", { class: same ? "cont" : null },
      h("button", { class: "ts", type: "button", title: "Play from here", onclick: () => onSeek && onSeek(s.start) }, clock(s.start)),
      h("span", {}, s.speaker && !same ? h("span", { class: `spk spk-${idx % 6}` }, spkDot(s.speaker), s.speaker) : null,
        mixed && s.language ? h("span", { class: "lang-tag", title: LANG_NAMES[s.language] || s.language }, s.language.toUpperCase()) : null,
        s.unclear ? h("em", { class: "unclear", title: "The automatic transcript could not read this part. Click the time to listen." }, s.text) : s.text));
  }));
}

/* Line-by-line transcript editor: text, speaker per line, and speaker renaming. */
function transcriptEditor(baseSegs, { onSave, onCancel, onSeek }) {
  const segs = baseSegs.map((x) => ({ ...x }));
  const speakers = orderOf(segs);
  const names = Object.fromEntries(speakers.map((n) => [n, n]));
  const selects = [];
  const renameRow = h("div", { class: "rename-row" });

  function nextSpeaker() {
    for (let i = 0; i < 26; i++) {
      const n = `Speaker ${String.fromCharCode(65 + i)}`;
      if (!speakers.includes(n)) return n;
    }
    return `Speaker ${speakers.length + 1}`;
  }
  function fillOptions(sel) {
    const current = sel.value;
    sel.replaceChildren(
      ...speakers.map((k) => h("option", { value: k }, names[k] || k)),
      h("option", { value: "" }, "No label"),
      h("option", { value: "__new" }, "+ New speaker"));
    sel.value = current;
  }
  function drawRename() {
    renameRow.replaceChildren(h("span", { class: "rename-label" }, "Speaker names"),
      ...speakers.map((k, i) => {
        const inp = h("input", { type: "text", value: names[k], maxlength: 60, "aria-label": `Name for ${k}`, class: `spk-${i % 6}` });
        inp.addEventListener("input", () => { names[k] = inp.value; selects.forEach(fillOptions); });
        return h("label", { class: `rename spk-${i % 6}` }, spkDot(k), inp);
      }),
      h("span", { class: "muted small" }, "Rename once to change every line (e.g. “Doctor”)."));
  }

  const rows = segs.map((sg) => {
    const sel = h("select", { class: "seg-speaker", "aria-label": "Speaker" });
    selects.push(sel);
    fillOptions(sel);
    sel.value = sg.speaker || "";
    sel.addEventListener("change", () => {
      if (sel.value === "__new") {
        const n = nextSpeaker();
        speakers.push(n); names[n] = n;
        selects.forEach(fillOptions);
        sel.value = n;
        drawRename();
      }
      sg.speaker = sel.value || null;
    });
    const ta = h("textarea", { class: "seg-text", rows: 1, "aria-label": `Text at ${clock(sg.start)}` }, sg.text);
    const grow = () => { ta.style.height = "auto"; ta.style.height = `${ta.scrollHeight}px`; };
    ta.addEventListener("input", () => { sg.text = ta.value; grow(); });
    requestAnimationFrame(grow);
    return h("div", { class: "seg-edit" },
      h("button", { class: "ts", type: "button", title: "Play from here", onclick: () => onSeek && onSeek(sg.start) }, clock(sg.start)),
      sel, ta);
  });
  drawRename();

  const saveBtn = h("button", { class: "btn primary", type: "button" }, icon("check"), "Save transcript");
  saveBtn.addEventListener("click", () => {
    saveBtn.disabled = true;
    const out = segs.map((sg) => ({
      start: sg.start, end: sg.end, text: sg.text, language: sg.language || null,
      speaker: sg.speaker ? ((names[sg.speaker] || sg.speaker).trim() || null) : null,
    }));
    Promise.resolve(onSave(out)).finally(() => { saveBtn.disabled = false; });
  });
  return h("div", { class: "editor" },
    h("p", { class: "muted small" }, "Correct the text and speakers line by line. Saving replaces the transcript; the recording itself is never changed."),
    speakers.length ? renameRow : null,
    h("div", { class: "seg-list" }, rows),
    h("div", { class: "editor-actions" }, saveBtn, h("button", { class: "btn", type: "button", onclick: onCancel }, "Cancel")));
}

/* MediaRecorder WebM files have no duration header; force the browser to compute it. */
function fixDuration(audio) {
  audio.addEventListener("loadedmetadata", function once() {
    audio.removeEventListener("loadedmetadata", once);
    if (audio.duration === Infinity || Number.isNaN(audio.duration)) {
      audio.addEventListener("durationchange", function reset() {
        if (!Number.isFinite(audio.duration)) return;
        audio.removeEventListener("durationchange", reset);
        audio.currentTime = 0;
      });
      audio.currentTime = 1e101; // seeks to the end (a ranged request), which reveals the length
    }
  });
}

/* ------------------------------------------------------------------ patient page */
async function viewPatient(pid) {
  let data;
  try { data = await api(`/api/patients/${pid}`); }
  catch (err) { return mount(backLink("#/", "Home"), h("div", { class: "notice err" }, err.message)); }
  let editing = false;
  draw();
  function draw() {
    const p = data.patient;
    const head = editing ? editForm(p) : h("header", { class: "ph" }, avatar(patientName(p), "lg"),
      h("div", { class: "ph-text" },
        h("h1", { class: "page-title" }, patientName(p)),
        h("p", { class: "page-sub" }, h("span", { class: "mono" }, p.patientId), patientDetails(p) ? h("span", {}, patientDetails(p)) : null)),
      h("button", { class: "btn", onclick: () => { editing = true; draw(); } }, icon("edit"), "Edit details"));
    mount(backLink("#/", "Home"), head,
      h("section", { class: "hero compact" },
        h("div", { class: "hero-text" }, h("h2", {}, "New consultation"), h("p", {}, "The recording is linked to this patient automatically.")),
        h("a", { class: "big new", href: `#/new?patient=${p.patientId}` }, icon("mic"), "Start")),
      h("section", { class: "section card" },
        h("div", { class: "section-head" }, h("h2", {}, icon("history"), `Consultations (${data.consultations.length})`)),
        data.consultations.length ? h("ul", { class: "list" }, data.consultations.map((c) => h("li", {}, h("a", { class: "row", href: `#/c/${c.consultationId}` },
          dateBadge(c.startedAt),
          h("span", { class: "p" }, h("span", {}, `${fmtTime(c.startedAt)}${c.durationSec != null ? ` · ${humanDuration(c.durationSec)}` : ""}`),
            h("span", { class: "sub" }, `${new Date(c.startedAt).getFullYear()} · ${c.doctorName || ""}`)),
          statusTags(c),
          icon("chevron", "chev"))))) : emptyState("mic", "No consultations yet.")));
  }
  function editForm(p) {
    const f = patientFields(p);
    return h("form", { class: "panel", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const res = await api(`/api/patients/${p.patientId}`, { method: "PATCH", body: f.body() });
        data.patient = res.patient; editing = false; draw(); toast("Patient updated");
      } catch (ex) { toast(ex.message); }
    } },
    h("h2", {}, h("span", { class: "ptitle" }, icon("user"), "Patient details")),
    f.el,
    h("div", { class: "btn-row" }, h("button", { class: "btn primary", type: "submit" }, "Save"), h("button", { class: "btn", type: "button", onclick: () => { editing = false; draw(); } }, "Cancel")));
  }
}

/* ------------------------------------------------------------------ boot */
(async function boot() {
  $("#topbar").replaceChildren(h("div", { class: "topbar-inner" }, h("span", { class: "spacer" }), themeToggle()));
  $("#topbar").hidden = false;
  try {
    state.config = await api("/api/config");
  } catch (err) {
    mount(h("div", { class: "notice err" }, h("strong", {}, "Cannot reach the server"), err.message));
    return;
  }
  document.title = state.config.appTitle;
  route();
})();
