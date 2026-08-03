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

  // Keep the notification bell badge live
  const notifBell = document.getElementById("notif-bell");
  if (notifBell) {
    setInterval(() => {
      fetch("/api/notifications/count")
        .then((r) => r.json())
        .then((data) => {
          let badge = document.getElementById("notif-count");
          if (data.count > 0) {
            if (!badge) {
              badge = document.createElement("span");
              badge.className = "bell-badge";
              badge.id = "notif-count";
              notifBell.appendChild(badge);
            }
            badge.textContent = data.count;
          } else if (badge) {
            badge.remove();
          }
        })
        .catch(() => {});
    }, 20000);
  }

  // ---- Sound + voice + browser popup alerts for new notifications (opt-in) ----
  const alertsBtn = document.getElementById("alerts-toggle");
  if (alertsBtn) {
    const alertsLabel = document.getElementById("alerts-toggle-label");
    const LAST_ID_KEY = "tm_last_notified_id";
    const ENABLED_KEY = "tm_alerts_enabled";
    let audioCtx = null;

    function alertsSupported() {
      return "Notification" in window && "speechSynthesis" in window;
    }

    function getAudioCtx() {
      // Browsers require audio to be unlocked by a user gesture first. We create/resume
      // this once, inside the toggle's click handler, then reuse it for every later ring
      // triggered automatically by polling (no gesture needed after that).
      if (!audioCtx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        audioCtx = new Ctx();
      }
      if (audioCtx.state === "suspended") audioCtx.resume();
      return audioCtx;
    }

    function playRingSound() {
      const ctx = getAudioCtx();
      if (!ctx) return;
      try {
        const now = ctx.currentTime;
        // A quick two-note "ding-dong" chime, synthesized directly - no audio file needed.
        [880, 1318.5].forEach((freq, i) => {
          const start = now + i * 0.16;
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = "sine";
          osc.frequency.value = freq;
          gain.gain.setValueAtTime(0.0001, start);
          gain.gain.exponentialRampToValueAtTime(0.35, start + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.4);
          osc.connect(gain).connect(ctx.destination);
          osc.start(start);
          osc.stop(start + 0.42);
        });
      } catch (e) {}
    }

    function setToggleUI(on) {
      alertsBtn.classList.toggle("alerts-on", on);
      alertsLabel.textContent = on ? "Alerts: on" : "Alerts: off";
    }

    function speak(text) {
      try {
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(text);
        utter.rate = 1.0;
        window.speechSynthesis.speak(utter);
      } catch (e) {}
    }

    function popup(message, url) {
      try {
        const n = new Notification("Team Manager", { body: message, icon: "/static/img/favicon-64.png" });
        n.onclick = () => {
          window.focus();
          window.location.href = url;
          n.close();
        };
      } catch (e) {}
    }

    function isEnabled() {
      return localStorage.getItem(ENABLED_KEY) === "true" && Notification.permission === "granted";
    }

    function checkForNewNotifications() {
      if (!isEnabled()) return;
      const afterId = localStorage.getItem(LAST_ID_KEY) || "0";
      fetch("/api/notifications/recent?after=" + afterId)
        .then((r) => r.json())
        .then((items) => {
          if (!items.length) return;
          items.forEach((n, i) => {
            setTimeout(() => {
              playRingSound();
              popup(n.message, n.url);
              setTimeout(() => speak(n.message), 450);
            }, i * 1200); // stagger multiple at once so rings/voices don't overlap
          });
          const maxId = Math.max(...items.map((n) => n.id));
          localStorage.setItem(LAST_ID_KEY, String(maxId));
        })
        .catch(() => {});
    }

    if (!alertsSupported()) {
      alertsBtn.style.display = "none";
    } else {
      setToggleUI(isEnabled());

      alertsBtn.addEventListener("click", () => {
        if (isEnabled()) {
          localStorage.setItem(ENABLED_KEY, "false");
          window.speechSynthesis.cancel();
          setToggleUI(false);
          return;
        }

        if (Notification.permission === "denied") {
          alert("Notifications are blocked for this site in your browser settings. " +
                "Enable them there (usually via the icon in the address bar) and try again.");
          return;
        }

        getAudioCtx(); // unlock audio playback now, while we have a user gesture

        Notification.requestPermission().then((permission) => {
          if (permission !== "granted") return;
          // Baseline to "now" so enabling doesn't suddenly announce a big backlog of
          // pre-existing unread notifications - only new ones from this point on.
          fetch("/api/notifications/recent?after=0")
            .then((r) => r.json())
            .then((items) => {
              const maxId = items.length ? Math.max(...items.map((n) => n.id)) : 0;
              localStorage.setItem(LAST_ID_KEY, String(maxId));
              localStorage.setItem(ENABLED_KEY, "true");
              setToggleUI(true);
              playRingSound();
              setTimeout(() => speak("Voice and popup alerts are now on."), 450);
            });
        });
      });

      setInterval(checkForNewNotifications, 15000);
    }
  }

  // initial heartbeat + interval
  if (manualStatus) {
    sendHeartbeat(manualStatus, true, isHeld);
  } else {
    sendHeartbeat("available", false, false);
  }
  setInterval(tick, HEARTBEAT_INTERVAL_MS);
})();
