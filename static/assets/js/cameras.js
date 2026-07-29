/* Camera list page: access modal, device modal, MQTT polling, copy helpers.
 * i18n: đọc từ <script id="camerasI18n" type="application/json"> do template render.
 * Không dùng inline <script>/onclick trong template (CSP-friendly). */
(function () {
  "use strict";

  // ── i18n ──────────────────────────────────────────────────────────────────
  var I18N = {};
  var i18nEl = document.getElementById("camerasI18n");
  if (i18nEl) {
    try { I18N = JSON.parse(i18nEl.textContent); } catch (e) { I18N = {}; }
  }
  function t(key, fallback) { return I18N[key] || fallback || key; }

  function notify(type, msg) {
    if (window.notyf && msg) {
      notyf.open({ type: type === "success" ? "success" : "error", message: msg });
    }
  }

  var SPINNER_HTML =
    '<div class="modal-body text-center py-5 text-muted">' +
    '<div class="spinner-border text-primary" role="status"></div>' +
    '<p class="mt-3 mb-0">' + t("loading", "Loading…") + "</p></div>";

  // ── Auto-open modal (thay inline script) ─────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".modal[data-auto-open]").forEach(function (el) {
      new bootstrap.Modal(el).show();
    });
  });

  // ── Copy-to-clipboard chung (thay inline onclick) ────────────────────────
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".js-copy");
    if (!btn) return;
    var target = document.querySelector(btn.dataset.copyTarget || "");
    if (!target) return;
    target.select && target.select();
    var value = target.value != null ? target.value : target.textContent;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(value).then(function () {
        var old = btn.textContent;
        btn.textContent = "✓";
        setTimeout(function () { btn.textContent = old === "✓" ? "⎘" : old; }, 1500);
      });
    }
  });

  // ══════════════════════════════════════════════════════════════════════════
  //  Access modal (phân quyền camera)
  // ══════════════════════════════════════════════════════════════════════════
  (function () {
    var modalEl = document.getElementById("modalCameraAccess");
    if (!modalEl) return;
    var contentEl = document.getElementById("cameraAccessContent");
    var bsModal = new bootstrap.Modal(modalEl);

    function csrf() {
      var el = contentEl.querySelector("input[name=csrfmiddlewaretoken]");
      return el ? el.value : "";
    }

    function showFlash() {
      var f = contentEl.querySelector("#accessFlash");
      if (!f) return;
      notify(f.dataset.level || "success", f.dataset.msg);
    }

    function initChips() {
      var searchInput = contentEl.querySelector("#user_search_input");
      var dropdown = contentEl.querySelector("#user_dropdown");
      var chipsArea = contentEl.querySelector("#chips_area");
      var hiddenUsers = contentEl.querySelector("#hidden_users");
      if (!searchInput) return;
      var selected = {};

      searchInput.addEventListener("input", function () {
        var q = this.value.trim().toLowerCase();
        if (!q) { dropdown.style.display = "none"; return; }
        var anyVisible = false;
        dropdown.querySelectorAll(".user-option").forEach(function (opt) {
          var match = opt.dataset.name.toLowerCase().includes(q);
          opt.style.display = match ? "" : "none";
          if (match) anyVisible = true;
        });
        dropdown.style.display = anyVisible ? "block" : "none";
      });

      dropdown.addEventListener("click", function (e) {
        var opt = e.target.closest(".user-option");
        if (!opt) return;
        var pk = opt.dataset.pk, name = opt.dataset.name;
        if (selected[pk]) return;
        selected[pk] = name;
        // Build chip bằng DOM API — KHÔNG innerHTML với dữ liệu người dùng (XSS)
        var chip = document.createElement("span");
        chip.className = "badge bg-primary d-inline-flex align-items-center gap-1 px-2 py-1";
        chip.style.cssText = "font-size:.8rem;font-weight:500";
        chip.dataset.pk = pk;
        var label = document.createElement("span");
        label.textContent = name;
        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.dataset.pk = pk;
        removeBtn.style.cssText = "background:none;border:none;color:inherit;padding:0 0 0 4px;font-size:1.1rem;line-height:1;cursor:pointer";
        removeBtn.innerHTML = "&times;";
        chip.appendChild(label);
        chip.appendChild(removeBtn);
        chipsArea.appendChild(chip);
        var inp = document.createElement("input");
        inp.type = "checkbox"; inp.name = "users"; inp.value = pk;
        inp.checked = true; inp.style.display = "none"; inp.id = "hid_" + pk;
        hiddenUsers.appendChild(inp);
        var ph = contentEl.querySelector("#chips_placeholder");
        if (ph) ph.style.display = "none";
        opt.style.display = "none";
        searchInput.value = "";
        dropdown.style.display = "none";
        searchInput.focus();
      });

      chipsArea.addEventListener("click", function (e) {
        var btn = e.target.closest("button[data-pk]");
        if (!btn) return;
        var pk = btn.dataset.pk;
        var chip = chipsArea.querySelector("span.badge[data-pk='" + pk + "']");
        if (chip) chip.remove();
        var inp = contentEl.querySelector("#hid_" + pk);
        if (inp) inp.remove();
        var opt = dropdown.querySelector(".user-option[data-pk='" + pk + "']");
        if (opt) opt.style.display = "";
        delete selected[pk];
        if (!chipsArea.querySelector("span.badge")) {
          var ph = contentEl.querySelector("#chips_placeholder");
          if (ph) ph.style.display = "";
        }
      });
    }

    function bindContent() {
      initChips();
      showFlash();
    }

    function postForm(url, formData) {
      return fetch(url, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrf() },
        body: formData
      }).then(function (r) { return r.text(); }).then(function (html) {
        contentEl.innerHTML = html;
        bindContent();
      });
    }

    document.addEventListener("click", function (e) {
      var opener = e.target.closest(".js-access-open");
      if (!opener) return;
      contentEl.innerHTML = SPINNER_HTML;
      bsModal.show();
      fetch(opener.dataset.accessUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.text(); })
        .then(function (html) { contentEl.innerHTML = html; bindContent(); });
    });

    contentEl.addEventListener("submit", function (e) {
      var form = e.target.closest("#grantForm, .js-edit-form");
      if (!form) return;
      e.preventDefault();
      postForm(form.dataset.ajaxUrl, new FormData(form));
    });

    contentEl.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-revoke");
      if (!btn) return;
      if (!confirm(t("revokeConfirm", "Revoke access for") + " " + btn.dataset.username + "?")) return;
      var fd = new FormData();
      fd.append("csrfmiddlewaretoken", csrf());
      postForm(btn.dataset.ajaxUrl, fd);
    });
  })();

  // ══════════════════════════════════════════════════════════════════════════
  //  Device / Live modal
  // ══════════════════════════════════════════════════════════════════════════
  (function () {
    var modalEl = document.getElementById("modalCameraDevice");
    if (!modalEl) return;
    var contentEl = document.getElementById("cameraDeviceContent");
    var bsModal = new bootstrap.Modal(modalEl);
    var root = null;         // #deviceModalContent (data-* URLs)
    var lastPhotoId = null;

    function csrf() {
      var el = contentEl.querySelector("input[name=csrfmiddlewaretoken]");
      return el ? el.value : "";
    }
    function spin(on) {
      var s = contentEl.querySelector("#devRefreshSpin");
      if (s) s.style.display = on ? "" : "none";
    }

    // ── Refresh latest photo ──
    function refresh() {
      if (!root) return;
      spin(true);
      fetch(root.dataset.latestUrl, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.photo) return;
          var img = contentEl.querySelector("#devLivePhoto");
          var empty = contentEl.querySelector("#devNoPhoto");
          if (data.photo.id !== lastPhotoId) {
            lastPhotoId = data.photo.id;
            if (empty) { empty.style.display = "none"; }
            if (img) {
              img.src = data.photo.url;
            } else {
              var wrap = contentEl.querySelector(".dev-photo-wrap");
              if (wrap) {
                img = document.createElement("img");
                img.id = "devLivePhoto";
                img.src = data.photo.url;
                wrap.insertBefore(img, wrap.firstChild);
              }
            }
          }
          var tEl = contentEl.querySelector("#devPhotoTime");
          if (tEl && data.photo.taken_at_display) tEl.textContent = data.photo.taken_at_display;
        })
        .catch(function () {})
        .finally(function () { spin(false); });
    }

    // ── Poll device state (sau khi gửi lệnh MQTT) ──
    function pollState(check, done, tries) {
      tries = tries == null ? 8 : tries;
      if (!root || !root.dataset.stateUrl || tries <= 0) { if (done) done(null); return; }
      setTimeout(function () {
        fetch(root.dataset.stateUrl, { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (check(d)) { if (done) done(d); }
            else pollState(check, done, tries - 1);
          })
          .catch(function () { pollState(check, done, tries - 1); });
      }, 1000);
    }

    function setOnlineBadge(online) {
      var badge = contentEl.querySelector("#devOnlineBadge");
      if (!badge) return;
      badge.classList.remove("bg-success-soft", "text-success", "bg-secondary-soft", "text-secondary");
      if (online) {
        badge.classList.add("bg-success-soft", "text-success");
        badge.textContent = "\u25cf " + t("online", "Online");
      } else {
        badge.classList.add("bg-secondary-soft", "text-secondary");
        badge.textContent = "\u25cb " + t("offline", "Offline");
      }
    }

    function setSyncBadge(inSync) {
      var badge = contentEl.querySelector("#camSettingsSyncBadge");
      if (!badge) return;
      badge.classList.remove("bg-success-soft", "text-success", "bg-secondary-soft", "text-secondary");
      if (inSync) {
        badge.classList.add("bg-success-soft", "text-success");
        badge.textContent = "✓ " + t("inSync", "In sync");
      } else {
        badge.classList.add("bg-secondary-soft", "text-secondary");
        badge.textContent = "◌ " + t("notSynced", "Not synced");
      }
    }

    // ── Save settings (interval / status) ──
    function saveSettings(payload) {
      return fetch(root.dataset.settingsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        credentials: "same-origin",
        body: JSON.stringify(payload)
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); });
    }

    function setStatusUI(status) {
      contentEl.querySelectorAll(".dev-status-btn").forEach(function (b) {
        var active = b.dataset.status === status;
        b.classList.remove("btn-success", "btn-warning", "btn-secondary", "btn-outline-secondary");
        if (active) {
          b.classList.add(status === "active" ? "btn-success" : status === "maintenance" ? "btn-warning" : "btn-secondary");
        } else {
          b.classList.add("btn-outline-secondary");
        }
      });
    }

    // ── Wake / capture now ──
    function wake() {
      var btn = contentEl.querySelector("#devBtnWake");
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      btn.textContent = "⏳ Đang chụp…";

      var prevTakenAt = (function () {
        var tEl = contentEl.querySelector("#devPhotoTime");
        return tEl ? tEl.textContent.trim() : "";
      }());

      fetch(root.dataset.wakeUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrf() },
        credentials: "same-origin"
      })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          notify("error", (res.data && res.data.error) || "Error");
          btn.disabled = false;
          btn.innerHTML = "⚡ " + t("captureNow", "Capture now");
          return;
        }
        notify("success", t("captureRequested", "Capture requested."));
        var note = contentEl.querySelector("#devWakeNote");
        if (note) note.style.display = "";

        // Poll ảnh mới mỗi 2s trong tối đa 50s (25 lần)
        var tries = 0, maxTries = 60;  // 60×2s = 120s
        var timer = setInterval(function () {
          tries++;
          fetch(root.dataset.latestUrl, { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              var takenAt = (data.photo && data.photo.taken_at_display) || "";
              if (takenAt && takenAt !== prevTakenAt) {
                clearInterval(timer);
                if (note) note.style.display = "none";
                refresh();
                notify("success", t("photoCaptured", "📷 Ảnh mới đã chụp xong!"));
                btn.disabled = false;
                btn.innerHTML = "⚡ " + t("captureNow", "Capture now");
              } else if (tries >= maxTries) {
                clearInterval(timer);
                btn.disabled = false;
                btn.innerHTML = "⚡ " + t("captureNow", "Capture now");
                notify("error", t("captureTimeout", "Chưa nhận được ảnh mới."));
              }
            })
            .catch(function () {
              clearInterval(timer);
              btn.disabled = false;
              btn.innerHTML = "⚡ " + t("captureNow", "Capture now");
            });
        }, 2000);
      })
      .catch(function () {
        notify("error", t("networkError", "Network error"));
        btn.disabled = false;
        btn.innerHTML = "⚡ " + t("captureNow", "Capture now");
      });
    }

    // ── Get SIM info ──
    function getSimInfo() {
      var btn = contentEl.querySelector("#devBtnSim");
      var simSpin = contentEl.querySelector(".dev-sim-spin");
      if (btn) btn.disabled = true;
      if (simSpin) simSpin.style.display = "";
      fetch(root.dataset.simUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrf() },
        credentials: "same-origin"
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) { notify("error", (res.data && res.data.error) || "Error"); return; }
          function renderSim(s) {
            function set(id, val) { var el = contentEl.querySelector(id); if (el) el.textContent = val || "—"; }
            set("#devSimNumber", s.number);
            set("#devSimOp", s.operator);
            set("#devSimIccid", s.iccid);
            set("#devSimSignal", s.signal_dbm != null ? (s.signal_dbm + " dBm · " + (s.signal_label || "")) : "—");
            set("#devSimDbm", s.signal_dbm != null ? (s.signal_dbm + " dBm") : "—");
            set("#devSimOperator", s.operator || s.signal_label);
          }
          renderSim(res.data.sim || {});
          var note = contentEl.querySelector("#devSimNote");
          if (res.data.sent) {
            if (note) note.style.display = "";
            var prev = (res.data.sim && res.data.sim.updated_at) || null;
            pollState(
              function (d) { return d && d.sim && d.sim.updated_at && d.sim.updated_at !== prev; },
              function (d) {
                if (simSpin) simSpin.style.display = "none";
                if (btn) btn.disabled = false;
                if (d) {
                  renderSim(d.sim);
                  if (note) note.style.display = "none";
                  notify("success", t("simUpdatedFromDevice", "SIM info updated from device."));
                } else {
                  notify("error", t("simNoResponse", "No SIM response yet."));
                }
              });
            return; // giữ spinner đến khi poll xong
          }
          if (note) note.style.display = res.data.sim_query_pending ? "" : "none";
          notify("success", t("simUpdated", "SIM info updated."));
          if (btn) btn.disabled = false;
          if (simSpin) simSpin.style.display = "none";
        })
        .catch(function () {
          if (btn) btn.disabled = false;
          if (simSpin) simSpin.style.display = "none";
        });
    }


    // ── Modal Live View ──────────────────────────────────────────────────
    var modalLv = (function () {
      var _running = false, _seq = 0, _timer = null, _failCount = 0, _prevUrl = null;

      function _img()   { return contentEl.querySelector("#devLvFrame"); }
      function _still() { return contentEl.querySelector("#devLivePhoto") ||
                                 contentEl.querySelector("#devNoPhoto"); }
      function _badge() { return contentEl.querySelector("#devLvBadge"); }
      function _btn()   { return contentEl.querySelector("#devBtnLv"); }

      function _showLive(on) {
        var img    = _img();
        var still  = contentEl.querySelector("#devLivePhoto");
        var noPhoto = contentEl.querySelector("#devNoPhoto");
        var badge  = _badge();
        var btn    = _btn();
        if (img)   img.style.display    = on ? "block" : "none";
        if (still) still.style.display  = on ? "none"  : "";
        if (noPhoto && !still) noPhoto.style.display = on ? "none" : "";
        if (badge) badge.style.display  = on ? ""     : "none";
        if (btn)   btn.textContent = on ? "◼ Stop" : "◉ Live";
        if (btn)   btn.classList.toggle("active", on);
      }

      function _poll() {
        if (!_running || !root) return;
        fetch(root.dataset.liveFrameUrl + "?seq=" + _seq, { credentials: "same-origin" })
          .then(function (r) {
            if (r.status === 200) {
              _failCount = 0;
              _seq = parseInt(r.headers.get("X-Frame-Seq") || _seq, 10);
              return r.blob().then(function (b) {
                var url = URL.createObjectURL(b);
                var img = _img();
                if (_prevUrl) URL.revokeObjectURL(_prevUrl);
                _prevUrl = url;
                if (img) { img.src = url; }
                _showLive(true);
              });
            }
            if (r.status === 204) { _failCount++; }
            // 304: frame chưa đổi — giữ nguyên
          })
          .catch(function () { _failCount++; })
          .finally(function () {
            if (!_running) return;
            if (_failCount > 30) { stop(); return; }
            _timer = setTimeout(_poll, 1000);
          });
      }

      function start() {
        if (!root || !root.dataset.liveStartUrl) return;
        fetch(root.dataset.liveStartUrl, {
          method: "POST", credentials: "same-origin",
          headers: { "X-CSRFToken": csrf() }
        }).then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d.ok) return;
            _running = true; _seq = 0; _failCount = 0;
            _showLive(true);
            _poll();
          });
      }

      function stop() {
        if (!_running) return;
        _running = false;
        if (_timer) { clearTimeout(_timer); _timer = null; }
        _showLive(false);
        if (root && root.dataset.liveStopUrl) {
          fetch(root.dataset.liveStopUrl, {
            method: "POST", credentials: "same-origin",
            headers: { "X-CSRFToken": csrf() }
          });
        }
        if (_prevUrl) { URL.revokeObjectURL(_prevUrl); _prevUrl = null; }
      }

      function toggle() { if (_running) stop(); else start(); }
      function cleanup() { stop(); }

      return { toggle: toggle, cleanup: cleanup };
    }());

    // ── Poll trạng thái online định kỳ khi modal mở ────────────────────
    var statePoll = (function () {
      var _timer = null;

      function _tick() {
        if (!root || !root.dataset.stateUrl) return;
        fetch(root.dataset.stateUrl, { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d || !d.ok) return;
            setOnlineBadge(!!d.online);
            if (d.settings) setSyncBadge(!!d.settings.in_sync);
          })
          .catch(function () {});
      }

      function start() {
        stop();
        _timer = setInterval(_tick, 10000);
      }
      function stop() {
        if (_timer) { clearInterval(_timer); _timer = null; }
      }
      return { start: start, stop: stop };
    }());

    // ── Bind after content loaded ──
    function bind() {
      root = contentEl.querySelector("#deviceModalContent");
      lastPhotoId = null;
      statePoll.start();

      var btnRefresh = contentEl.querySelector("#devBtnRefresh");
      if (btnRefresh) btnRefresh.addEventListener("click", refresh);

      var btnWake = contentEl.querySelector("#devBtnWake");
      if (btnWake) btnWake.addEventListener("click", wake);

      var btnSim = contentEl.querySelector("#devBtnSim");
      if (btnSim) btnSim.addEventListener("click", getSimInfo);

      contentEl.querySelectorAll(".dev-status-btn").forEach(function (b) {
        b.addEventListener("click", function () {
          saveSettings({ status: b.dataset.status }).then(function (res) {
            if (res.ok) { setStatusUI(res.data.status); notify("success", t("statusUpdated", "Status updated.")); }
            else notify("error", (res.data && res.data.error) || "Error");
          });
        });
      });

      var btnSave = contentEl.querySelector("#devBtnSaveSettings");
      if (btnSave) btnSave.addEventListener("click", function () {
        var inp = contentEl.querySelector("#devInterval");
        var val = parseInt(inp.value, 10);
        if (isNaN(val) || val < 30 || val > 86400) { notify("error", "30 – 86400 sec"); return; }
        btnSave.disabled = true;
        saveSettings({ capture_interval_sec: val }).then(function (res) {
          if (res.ok) notify("success", t("settingsSaved", "Settings saved."));
          else notify("error", (res.data && res.data.error) || "Error");
        }).finally(function () { btnSave.disabled = false; });
      });

      // ── Camera imaging settings (gửi máy ảnh qua MQTT + poll ack) ──
      var camForm = contentEl.querySelector("#camSettingsForm");
      if (camForm) camForm.addEventListener("submit", function (e) {
        e.preventDefault();
        if (!root) return;
        var url = root.dataset.cameraSettingsUrl;
        if (!url) return;
        var payload = {};
        camForm.querySelectorAll("input, select").forEach(function (el) {
          if (!el.name) return;
          if (el.type === "checkbox") { payload[el.name] = el.checked; }
          else { payload[el.name] = el.value; }
        });
        var saveBtn = contentEl.querySelector("#camSettingsSave");
        var camSpin = contentEl.querySelector(".cam-settings-spin");
        if (saveBtn) saveBtn.disabled = true;
        if (camSpin) camSpin.style.display = "";
        fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          credentials: "same-origin",
          body: JSON.stringify(payload)
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            if (res.ok) {
              if (res.data.sent) {
                notify("success", t("savedSending", "Saved. Sending to camera…"));
                setSyncBadge(false);
                pollState(
                  function (d) { return d && d.settings && d.settings.in_sync; },
                  function (d) {
                    if (d) { setSyncBadge(true); notify("success", t("cameraApplied", "Camera applied the settings.")); }
                    else { notify("error", t("noResponseWillSync", "No response from camera yet.")); }
                  });
              } else {
                notify("success", t("camSettingsSaved", "Camera settings saved."));
                setSyncBadge(false);
              }
            } else {
              notify("error", (res.data && res.data.error) || "Error");
            }
          })
          .catch(function () { notify("error", t("networkError", "Network error")); })
          .finally(function () {
            if (saveBtn) saveBtn.disabled = false;
            if (camSpin) camSpin.style.display = "none";
          });
      });

      // ── Pull config from hardware ──
      var camPull = contentEl.querySelector("#camSettingsPull");
      if (camPull) camPull.addEventListener("click", function () {
        if (!root) return;
        var url = root.dataset.cameraPullUrl;
        if (!url) return;
        var pullSpin = contentEl.querySelector(".cam-pull-spin");
        camPull.disabled = true;
        if (pullSpin) pullSpin.style.display = "";
        fetch(url, {
          method: "POST",
          headers: { "X-CSRFToken": csrf() },
          credentials: "same-origin"
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            if (!res.ok) { notify("error", (res.data && res.data.error) || "Error"); return; }
            function fillForm(s) {
              var form = contentEl.querySelector("#camSettingsForm");
              if (!form) return;
              form.querySelectorAll("input, select").forEach(function (el) {
                if (!el.name || !(el.name in s)) return;
                if (el.type === "checkbox") { el.checked = !!s[el.name]; }
                else { el.value = s[el.name] == null ? "" : s[el.name]; }
              });
            }
            if (res.data.sent) {
              notify("success", t("requestedWaiting", "Requested — waiting for camera…"));
              var prevSyncedAt = res.data.last_synced_at || null;
              pollState(
                function (d) {
                  // Chờ last_synced_at MỚI HƠN (camera vừa reply)
                  return d && d.settings && d.settings.in_sync &&
                         d.settings.last_synced_at &&
                         d.settings.last_synced_at !== prevSyncedAt;
                },
                function (d) {
                  if (d) {
                    fillForm(d.settings.applied);
                    setSyncBadge(true);
                    notify("success", t("configLoaded", "Configuration loaded from camera."));
                  } else {
                    fillForm(res.data.settings || {});
                    notify("error", t("noResponseLastKnown", "No response — showing last known values."));
                  }
                }, 15);
            } else {
              fillForm(res.data.settings || {});
              notify("error", t("mqttUnavailable", "MQTT unavailable — showing last known values."));
            }
          })
          .catch(function () { notify("error", t("networkError", "Network error")); })
          .finally(function () {
            camPull.disabled = false;
            if (pullSpin) pullSpin.style.display = "none";
          });
      });
    }

    modalEl.addEventListener("hidden.bs.modal", function () { modalLv.cleanup(); statePoll.stop(); });
    document.addEventListener("click", function (e) {
      if (e.target.closest("#devBtnLv")) modalLv.toggle();
    });

    document.addEventListener("click", function (e) {
      var opener = e.target.closest(".js-device-open");
      if (!opener) return;
      e.preventDefault();
      contentEl.innerHTML = SPINNER_HTML;
      bsModal.show();
      fetch(opener.dataset.deviceUrl, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
        .then(function (r) { return r.text(); })
        .then(function (html) { modalLv.cleanup(); contentEl.innerHTML = html; bind(); refresh(); });
    });

    // ── Kích hoạt từ xa ngay trên card (không mở modal) ──
    function cardCsrf() {
      var el = document.querySelector("input[name=csrfmiddlewaretoken]");
      return el ? el.value : "";
    }
    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-card-wake");
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      var wakeSpin = btn.querySelector(".js-wake-spin");
      var ic = btn.querySelector(".js-wake-ic");
      btn.disabled = true;
      if (wakeSpin) wakeSpin.style.display = "";
      if (ic) ic.style.display = "none";
      fetch(btn.dataset.wakeUrl, {
        method: "POST",
        headers: { "X-CSRFToken": cardCsrf() },
        credentials: "same-origin"
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (res.ok) {
            notify("success", t("activationSent", "Activation sent."));
            btn.classList.remove("btn-primary");
            btn.classList.add("btn-success");
            setTimeout(function () {
              btn.classList.remove("btn-success");
              btn.classList.add("btn-primary");
            }, 2500);
          } else {
            notify("error", (res.data && res.data.error) || "Error");
          }
        })
        .catch(function () { notify("error", t("networkError", "Network error")); })
        .finally(function () {
          btn.disabled = false;
          if (wakeSpin) wakeSpin.style.display = "none";
          if (ic) ic.style.display = "";
        });
    });
  })();
})();
