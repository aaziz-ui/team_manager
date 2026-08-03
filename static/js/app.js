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

  // ---- Loud ring + browser popup alerts for new notifications (default ON) ----
  const alertsBtn = document.getElementById("alerts-toggle");
  if (alertsBtn) {
    const alertsLabel = document.getElementById("alerts-toggle-label");
    const LAST_ID_KEY = "tm_last_notified_id";
    const ENABLED_KEY = "tm_alerts_enabled";
    let audioCtx = null;
    let audioUnlocked = false;

    function alertsSupported() {
      return "Notification" in window && !!(window.AudioContext || window.webkitAudioContext);
    }

    function getAudioCtx() {
      if (!audioCtx) {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        audioCtx = new Ctx();
      }
      if (audioCtx.state === "suspended") audioCtx.resume();
      return audioCtx;
    }

    function ringOnce(startOffset, ctx) {
      const now = ctx.currentTime + startOffset;
      // Two-note "ding-dong", loud and bright.
      [880, 1318.5].forEach((freq, i) => {
        const start = now + i * 0.16;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "triangle"; // brighter, more piercing than a plain sine - carries further
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.9, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.45);
        osc.connect(gain).connect(ctx.destination);
        osc.start(start);
        osc.stop(start + 0.47);
      });
    }

    function playRingSound() {
      const ctx = getAudioCtx();
      if (!ctx) return;
      try {
        // Ring twice back-to-back so it's hard to miss.
        ringOnce(0, ctx);
        ringOnce(0.55, ctx);
      } catch (e) {}
    }

    function setToggleUI(on) {
      alertsBtn.classList.toggle("alerts-on", on);
      alertsLabel.textContent = on ? "Alerts: on" : "Alerts: off";
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

    // Alerts are ON by default for everyone - the only way to get "off" is an explicit
    // click that sets this to "false". No stored value at all (first time ever opening
    // the app) is treated as wanting alerts on.
    function wantsEnabled() {
      return localStorage.getItem(ENABLED_KEY) !== "false";
    }

    function isEnabled() {
      return wantsEnabled() && Notification.permission === "granted";
    }

    function baselineToNow(then) {
      fetch("/api/notifications/recent?after=0")
        .then((r) => r.json())
        .then((items) => {
          const maxId = items.length ? Math.max(...items.map((n) => n.id)) : 0;
          localStorage.setItem(LAST_ID_KEY, String(maxId));
          if (then) then();
        })
        .catch(() => { if (then) then(); });
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
            }, i * 1400); // stagger multiple at once so the rings don't overlap
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

      // Browsers require a real user gesture before they'll grant notification permission
      // or play audio - no site can silently turn these on with zero interaction, that's a
      // security restriction, not something this app can bypass. So instead of making
      // someone hunt for a button, we unlock everything automatically the moment they do
      // *anything* on the page - click a nav link, log in, whatever - so in practice it
      // switches itself on within their first couple of seconds of using the app, with no
      // separate "please enable" step required.
      function unlockOnFirstInteraction() {
        if (audioUnlocked) return;
        audioUnlocked = true;
        getAudioCtx();
        if (wantsEnabled() && Notification.permission === "default") {
          Notification.requestPermission().then((permission) => {
            if (permission === "granted") {
              baselineToNow(() => setToggleUI(true));
            }
          });
        } else if (isEnabled()) {
          setToggleUI(true);
        }
      }
      ["click", "keydown", "touchstart"].forEach((evt) => {
        document.addEventListener(evt, unlockOnFirstInteraction, { once: true, passive: true });
      });

      // Also try immediately on page load - some browsers allow this without a gesture
      // if the person has "engaged" with the site before (e.g. visited it a few times).
      if (wantsEnabled() && Notification.permission === "default") {
        Notification.requestPermission().then((permission) => {
          if (permission === "granted") baselineToNow(() => setToggleUI(true));
        });
      }

      alertsBtn.addEventListener("click", () => {
        if (isEnabled()) {
          localStorage.setItem(ENABLED_KEY, "false");
          setToggleUI(false);
          return;
        }

        if (Notification.permission === "denied") {
          alert("Notifications are blocked for this site in your browser settings. " +
                "Enable them there (usually via the icon in the address bar) and try again.");
          return;
        }

        getAudioCtx(); // unlock audio playback now, while we have a user gesture
        audioUnlocked = true;

        Notification.requestPermission().then((permission) => {
          if (permission !== "granted") return;
          localStorage.setItem(ENABLED_KEY, "true");
          baselineToNow(() => {
            setToggleUI(true);
            playRingSound();
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
