(function () {
  const IDLE_THRESHOLD_MS = 5 * 60 * 1000;      // 5 minutes of no activity -> idle
  const HEARTBEAT_INTERVAL_MS = 30 * 1000;       // ping server every 30s

  let lastActivity = Date.now();
  let manualStatus = null;      // set when the user clicks a manual status button
  let lastSentStatus = null;

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

  function sendHeartbeat(status, manual) {
    fetch("/api/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: status, manual: !!manual }),
    }).catch(() => {});
    lastSentStatus = status;
  }

  function tick() {
    if (document.hidden) return; // don't report activity while tab is backgrounded

    if (manualStatus) {
      // Manual status persists until the user clicks "Available" again,
      // but we still send periodic heartbeats so last_seen_at stays fresh.
      if (lastSentStatus !== manualStatus) sendHeartbeat(manualStatus, true);
      else sendHeartbeat(manualStatus, true);
      return;
    }
    const status = computeAutoStatus();
    sendHeartbeat(status, false);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      markActivity();
      if (!manualStatus) sendHeartbeat("available", false);
    }
  });

  window.addEventListener("beforeunload", () => {
    navigator.sendBeacon &&
      navigator.sendBeacon(
        "/api/heartbeat",
        new Blob([JSON.stringify({ status: "offline", manual: true })], { type: "application/json" })
      );
  });

  // Manual status buttons on the dashboard
  document.querySelectorAll(".status-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const status = btn.dataset.status;
      document.querySelectorAll(".status-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      manualStatus = status === "available" ? null : status;
      sendHeartbeat(status, true);
    });
  });

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
            dot.className = "dot status-" + row.status;
            text.textContent = row.label;
          });
        })
        .catch(() => {});
    }, 20000);
  }

  // initial heartbeat + interval
  sendHeartbeat("available", false);
  setInterval(tick, HEARTBEAT_INTERVAL_MS);
})();
