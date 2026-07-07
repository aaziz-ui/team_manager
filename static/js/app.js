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
    if (isHeld && manualStatus) {
      // Held status keeps refreshing even while the tab is minimized/backgrounded.
      sendHeartbeat(manualStatus, true, true);
      return;
    }
    if (document.hidden) return; // don't report activity while tab is backgrounded (unheld)

    if (manualStatus) {
      sendHeartbeat(manualStatus, true, false);
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

  window.addEventListener("beforeunload", () => {
    navigator.sendBeacon &&
      navigator.sendBeacon(
        "/api/heartbeat",
        new Blob([JSON.stringify({ status: "offline", manual: true, hold: false })], { type: "application/json" })
      );
  });

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

  // Poll the team presence board every 20s if it's on the page
  const board = document.getElementById("status-board");
  if (board) {
    setInterval(() => {
      fetch("/api/status-board")
        .then((r) => r.json())
        .then((rows) => {
          rows.forEach((row) => {
            const tr = board.querySelector(`tr[data-user-id="${row.id}"]`);
            if (!tr) return;
            const dot = tr.querySelector(".dot");
            const text = tr.querySelector(".status-text");
            const lockBadge = tr.querySelector(".lock-badge");
            dot.className = "dot status-" + row.status;
            text.textContent = row.label;
            if (lockBadge) lockBadge.style.display = row.locked ? "inline-block" : "none";
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
