/**
 * poll-worker.js — Web Worker for terminal output polling and heartbeat.
 *
 * Runs in a Web Worker so it is NOT throttled by the browser when the tab
 * is in the background.
 *
 * Supports two polling modes (configured via POLL_MODE env var in app.yaml):
 *   "default" — per-pane polling via /api/output (simpler, independent retries)
 *   "batch"   — single /api/output-batch request for all panes
 *
 * Message protocol (main → worker):
 *   { type: 'init',              pollMode }         — set polling mode
 *   { type: 'start_poll',        paneId, sessionId }
 *   { type: 'stop_poll',         paneId }
 *   { type: 'visibility_change', hidden: bool }
 *
 * Message protocol (worker → main):
 *   { type: 'output',            paneId, data }
 *   { type: 'session_ended',     paneId, reason }
 *   { type: 'connection_status', paneId, status, attempt, maxAttempts }
 *   { type: 'session_dead',      paneId }
 */

/* eslint-env worker */
"use strict";

// ── Constants ─────────────────────────────────────────────────────────────
const POLL_INTERVAL_FG = 100;        // ms — foreground poll
const HEARTBEAT_INTERVAL_BG = 30000; // ms — background heartbeat
const RETRY_BASE_MS = 500;
const RETRY_MULTIPLIER = 2;
const RETRY_MAX_DELAY_MS = 10000;
const RETRY_MAX_ATTEMPTS = 8;
const SILENT_RETRY_THRESHOLD = 5;  // Don't show banner until this many consecutive failures

// ── State ─────────────────────────────────────────────────────────────────
let pollMode = "default";  // "default" or "batch"

// Per-pane state: { sessionId, timerId, retryCount }
const panes = new Map();

let globalHidden = false;

// Batch mode only
let batchTimerId = null;
let batchRetryCount = 0;

// ── Retry helpers ─────────────────────────────────────────────────────────

function retryDelay(attempt) {
  const base = RETRY_BASE_MS * Math.pow(RETRY_MULTIPLIER, attempt);
  const capped = Math.min(base, RETRY_MAX_DELAY_MS);
  return capped * (0.5 + Math.random());
}

function notifyRetry(paneId, retryCount) {
  if (retryCount >= SILENT_RETRY_THRESHOLD) {
    const visibleAttempt = retryCount - SILENT_RETRY_THRESHOLD + 1;
    const visibleMax = RETRY_MAX_ATTEMPTS - SILENT_RETRY_THRESHOLD + 1;
    self.postMessage({
      type: "connection_status", paneId,
      status: "reconnecting",
      attempt: visibleAttempt, maxAttempts: visibleMax,
    });
  }
}

function notifyReconnected(paneId, retryCount) {
  if (retryCount >= SILENT_RETRY_THRESHOLD) {
    self.postMessage({
      type: "connection_status", paneId,
      status: "connected", attempt: 0, maxAttempts: RETRY_MAX_ATTEMPTS,
    });
  }
}

function getRetryDelay(retryCount) {
  return retryCount < SILENT_RETRY_THRESHOLD
    ? RETRY_BASE_MS
    : retryDelay(retryCount - SILENT_RETRY_THRESHOLD);
}

// ═══════════════════════════════════════════════════════════════════════════
// DEFAULT MODE — per-pane polling via /api/output
// ═══════════════════════════════════════════════════════════════════════════

async function panePoll(paneId) {
  const state = panes.get(paneId);
  if (!state) return;

  try {
    const resp = await fetch("/api/output", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId }),
    });

    if (!resp.ok) {
      if (resp.status === 403) {
        self.postMessage({ type: "session_ended", paneId, reason: "auth_expired" });
        stopPane(paneId);
        return;
      }
      if (resp.status === 404) {
        self.postMessage({ type: "session_ended", paneId, reason: "exited" });
        stopPane(paneId);
        return;
      }
      throw new Error(`HTTP ${resp.status}`);
    }

    // Success — reset retry count
    const prevRetry = state.retryCount;
    state.retryCount = 0;
    if (prevRetry >= SILENT_RETRY_THRESHOLD) {
      notifyReconnected(paneId, prevRetry);
    }

    const data = await resp.json();

    if (data.shutting_down) {
      self.postMessage({ type: "session_ended", paneId, reason: "shutting_down" });
      handlePaneRetry(paneId, new Error("Server shutting down"));
      return;
    }

    if (data.timeout_warning) {
      self.postMessage({
        type: "output", paneId,
        data: { timeout_warning: true, output: "", exited: false, shutting_down: false },
      });
    }

    if (data.output) {
      self.postMessage({ type: "output", paneId, data });
    }

    if (data.exited) {
      self.postMessage({ type: "session_ended", paneId, reason: "exited" });
      stopPane(paneId);
    }
  } catch (err) {
    handlePaneRetry(paneId, err);
  }
}

function handlePaneRetry(paneId, err) {
  const state = panes.get(paneId);
  if (!state) return;

  state.retryCount++;

  if (state.retryCount > RETRY_MAX_ATTEMPTS) {
    self.postMessage({ type: "session_dead", paneId });
    stopPane(paneId);
    return;
  }

  notifyRetry(paneId, state.retryCount);
  clearPaneTimer(paneId);

  const delay = getRetryDelay(state.retryCount);
  state.timerId = setTimeout(() => {
    notifyReconnected(paneId, state.retryCount);
    startPaneTimer(paneId);
  }, delay);
}

function clearPaneTimer(paneId) {
  const state = panes.get(paneId);
  if (state && state.timerId) {
    clearInterval(state.timerId);
    clearTimeout(state.timerId);
    state.timerId = null;
  }
}

function startPaneTimer(paneId) {
  clearPaneTimer(paneId);
  const state = panes.get(paneId);
  if (!state) return;

  if (globalHidden) {
    panePoll(paneId);  // one heartbeat poll
    state.timerId = setInterval(() => panePoll(paneId), HEARTBEAT_INTERVAL_BG);
  } else {
    panePoll(paneId);
    state.timerId = setInterval(() => panePoll(paneId), POLL_INTERVAL_FG);
  }
}

function stopPane(paneId) {
  clearPaneTimer(paneId);
  panes.delete(paneId);
}

// ═══════════════════════════════════════════════════════════════════════════
// BATCH MODE — single /api/output-batch request for all panes
// ═══════════════════════════════════════════════════════════════════════════

async function batchPoll() {
  if (panes.size === 0) return;

  const sessionIds = [];
  const sidToPaneId = new Map();
  for (const [paneId, state] of panes) {
    sessionIds.push(state.sessionId);
    sidToPaneId.set(state.sessionId, paneId);
  }

  try {
    const resp = await fetch("/api/output-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_ids: sessionIds }),
    });

    if (!resp.ok) {
      if (resp.status === 403) {
        for (const paneId of panes.keys()) {
          self.postMessage({ type: "session_ended", paneId, reason: "auth_expired" });
        }
        stopAllPanes();
        return;
      }
      throw new Error(`HTTP ${resp.status}`);
    }

    batchRetryCount = 0;
    const result = await resp.json();

    if (result.shutting_down) {
      for (const paneId of panes.keys()) {
        self.postMessage({ type: "session_ended", paneId, reason: "shutting_down" });
      }
      handleBatchRetry(new Error("Server shutting down"));
      return;
    }

    for (const [sid, data] of Object.entries(result.outputs || {})) {
      const paneId = sidToPaneId.get(sid);
      if (!paneId) continue;

      self.postMessage({ type: "output", paneId, data });

      if (data.exited) {
        self.postMessage({ type: "session_ended", paneId, reason: "exited" });
        panes.delete(paneId);
      }
    }
  } catch (err) {
    handleBatchRetry(err);
  }
}

async function batchHeartbeat() {
  if (panes.size === 0) return;

  const sessionIds = [];
  for (const state of panes.values()) {
    sessionIds.push(state.sessionId);
  }

  try {
    const resp = await fetch("/api/output-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_ids: sessionIds }),
    });

    if (!resp.ok) {
      if (resp.status === 403) {
        for (const paneId of panes.keys()) {
          self.postMessage({ type: "session_ended", paneId, reason: "auth_expired" });
        }
        stopAllPanes();
        return;
      }
      throw new Error(`HTTP ${resp.status}`);
    }

    batchRetryCount = 0;

    const result = await resp.json();
    for (const [sid, data] of Object.entries(result.outputs || {})) {
      if (data.timeout_warning) {
        for (const [paneId, state] of panes) {
          if (state.sessionId === sid) {
            self.postMessage({
              type: "output", paneId,
              data: { timeout_warning: true, output: "", exited: false, shutting_down: false },
            });
          }
        }
      }
    }
  } catch (err) {
    handleBatchRetry(err);
  }
}

function handleBatchRetry(err) {
  batchRetryCount++;

  if (batchRetryCount > RETRY_MAX_ATTEMPTS) {
    for (const paneId of panes.keys()) {
      self.postMessage({ type: "session_dead", paneId });
    }
    stopAllPanes();
    return;
  }

  if (batchRetryCount >= SILENT_RETRY_THRESHOLD) {
    const visibleAttempt = batchRetryCount - SILENT_RETRY_THRESHOLD + 1;
    const visibleMax = RETRY_MAX_ATTEMPTS - SILENT_RETRY_THRESHOLD + 1;
    for (const paneId of panes.keys()) {
      self.postMessage({
        type: "connection_status", paneId,
        status: "reconnecting",
        attempt: visibleAttempt, maxAttempts: visibleMax,
      });
    }
  }

  clearBatchTimer();
  const delay = getRetryDelay(batchRetryCount);
  batchTimerId = setTimeout(() => {
    if (batchRetryCount >= SILENT_RETRY_THRESHOLD) {
      for (const paneId of panes.keys()) {
        self.postMessage({
          type: "connection_status", paneId,
          status: "connected", attempt: 0, maxAttempts: RETRY_MAX_ATTEMPTS,
        });
      }
    }
    startBatchTimer();
  }, delay);
}

function clearBatchTimer() {
  if (batchTimerId) {
    clearInterval(batchTimerId);
    clearTimeout(batchTimerId);
    batchTimerId = null;
  }
}

function startBatchTimer() {
  clearBatchTimer();
  if (panes.size === 0) return;

  if (globalHidden) {
    batchHeartbeat();
    batchTimerId = setInterval(() => batchHeartbeat(), HEARTBEAT_INTERVAL_BG);
  } else {
    batchPoll();
    batchTimerId = setInterval(() => batchPoll(), POLL_INTERVAL_FG);
  }
}

function stopAllPanes() {
  if (pollMode === "batch") {
    clearBatchTimer();
  } else {
    for (const paneId of panes.keys()) {
      clearPaneTimer(paneId);
    }
  }
  panes.clear();
}

// ═══════════════════════════════════════════════════════════════════════════
// Unified start/stop based on poll mode
// ═══════════════════════════════════════════════════════════════════════════

function startPolling(paneId) {
  if (pollMode === "batch") {
    startBatchTimer();
  } else {
    startPaneTimer(paneId);
  }
}

function restartAllTimers() {
  if (pollMode === "batch") {
    startBatchTimer();
  } else {
    for (const paneId of panes.keys()) {
      startPaneTimer(paneId);
    }
  }
}

// ── Message handler ───────────────────────────────────────────────────────

self.onmessage = function (event) {
  const msg = event.data;

  switch (msg.type) {
    case "init":
      pollMode = msg.pollMode === "batch" ? "batch" : "default";
      break;

    case "start_poll":
      panes.set(msg.paneId, { sessionId: msg.sessionId, timerId: null, retryCount: 0 });
      startPolling(msg.paneId);
      break;

    case "stop_poll":
      if (pollMode === "default") {
        stopPane(msg.paneId);
      } else {
        panes.delete(msg.paneId);
        if (panes.size === 0) clearBatchTimer();
      }
      break;

    case "visibility_change":
      globalHidden = msg.hidden;
      restartAllTimers();
      break;
  }
};
