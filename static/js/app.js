(function () {
  const IDLE_THRESHOLD_MS = 5 * 60 * 1000;      // 5 minutes of no activity -> idle
  const HEARTBEAT_INTERVAL_MS = 30 * 1000;       // ping server every 30s

  let lastActivity = Date.now();
  let manualStatus = null;      // set when the user clicks a manual status button
  let isHeld = false;           // "hold this status" checkbox state

  const statusButtonsWrap = document.getElementById("status-buttons");
  const holdCheckbox = document.getElementById("hold-status");

  if (statusButtonsWrap) {
    manualStatus = statusButtonsWrap.dataset.currentStatus === "available"
      ? null : statusButtonsWrap.dataset.currentStatus;
    isHeld = statusButtonsWrap.dataset.currentLocked === "true";
    if (holdCheckbox) holdCheckbox.checked = isHeld;
    highlightActiveButton(statusButtonsWrap.dataset.currentStatus);
  }

  function highlightActiveButton(status) {
    document.querySelectorAll(".status-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.status === status);
    });
  }

  function markActivity() {
    lastActivity = Date.now();
  }
  ["mousemove", "keydown", "click", "scroll", "touchstart"].forEach((evt) => {
    document.addEventListener(evt, markActivity, { passive: true });
  });

  function computeAutoStatus() {
    const idleFor = Date.now() - lastActivity;
    return idleFor > IDLE_THRESHOLD_MS ? "idle" : "available";
  }

  function sendHeartbeat(status, manual, hold) {
    fetch("/api/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: status, manual: !!manual, hold: !!hold }),
    }).catch(() => {});
  }

  function tick() {
    // Heartbeats keep going even while the tab is minimized/backgrounded - only
    // actually closing the browser (no JS running at all) should ever show Offline.
    if (manualStatus) {
      sendHeartbeat(manualStatus, true, isHeld);
      return;
    }
    const status = computeAutoStatus();
    sendHeartbeat(status, false, false);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      markActivity();
      if (!manualStatus) sendHeartbeat("available", false, false);
    }
  });

  // Note: we deliberately don't send an "offline" signal on beforeunload/pagehide.
  // Those events fire on refresh and normal in-app navigation too, not just when the
  // browser actually closes, and there's no reliable way to tell those apart from JS.
  // Instead we rely on the server-side staleness check: if no heartbeat arrives for
  // ~3 minutes (browser truly closed), the person shows as Offline automatically.

  // Manual status buttons on the dashboard
  document.querySelectorAll(".status-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const status = btn.dataset.status;
      highlightActiveButton(status);
      manualStatus = status === "available" ? null : status;
      isHeld = manualStatus && holdCheckbox ? holdCheckbox.checked : false;
      sendHeartbeat(status, true, isHeld);
    });
  });

  if (holdCheckbox) {
    holdCheckbox.addEventListener("change", () => {
      isHeld = holdCheckbox.checked;
      if (manualStatus) {
        sendHeartbeat(manualStatus, true, isHeld);
      }
    });
  }

  const noteInput = document.getElementById("status-note");
  const saveNoteBtn = document.getElementById("save-note-btn");
  if (saveNoteBtn && noteInput) {
    saveNoteBtn.addEventListener("click", () => {
      fetch("/api/status-note", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: noteInput.value }),
      }).catch(() => {});
    });
  }

  // Keep any table with rows tagged data-user-id in sync with live status.
  // Both "Team presence" and "My team" use this so they can never drift apart.
  function applyStatusRow(tr, row) {
    const dot = tr.querySelector(".dot");
    const text = tr.querySelector(".status-text");
    const lockBadge = tr.querySelector(".lock-badge");
    if (dot) dot.className = "dot status-" + row.status;
    if (text) text.textContent = row.label;
    if (lockBadge) lockBadge.style.display = row.locked ? "inline-block" : "none";
    let noteEl = tr.querySelector(".status-note");
    if (row.note) {
      if (!noteEl) {
        noteEl = document.createElement("div");
        noteEl.className = "muted small status-note";
        const statusCell = tr.querySelector("td:nth-child(2)");
        if (statusCell) statusCell.appendChild(noteEl);
      }
      noteEl.textContent = row.note;
    } else if (noteEl) {
      noteEl.remove();
    }
  }

  const statusTables = [
    document.getElementById("status-board"),
    document.getElementById("team-overview-board"),
  ].filter(Boolean);

  if (statusTables.length) {
    setInterval(() => {
      fetch("/api/status-board")
        .then((r) => r.json())
        .then((rows) => {
          statusTables.forEach((table) => {
            rows.forEach((row) => {
              const tr = table.querySelector(`tr[data-user-id="${row.id}"]`);
              if (tr) applyStatusRow(tr, row);
            });
          });
        })
        .catch(() => {});
    }, 20000);
  }

  // initial heartbeat + interval
  if (manualStatus) {
    sendHeartbeat(manualStatus, true, isHeld);
  } else {
    sendHeartbeat("available", false, false);
  }
  setInterval(tick, HEARTBEAT_INTERVAL_MS);
})();
