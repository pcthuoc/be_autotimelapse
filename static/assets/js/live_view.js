/* Live View realtime: start/stop session + polling frame JPEG tu Redis.
 * Markup yeu cau #liveViewBox voi data-start-url / data-stop-url / data-frame-url.
 */
(function () {
  "use strict";

  var box = document.getElementById("liveViewBox");
  if (!box) return;

  var btn = document.getElementById("lvToggle");
  var img = document.getElementById("lvFrame");
  var stateEl = document.getElementById("lvState");
  var running = false;
  var seq = 0;
  var timer = null;
  var failCount = 0;

  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }

  function setState(text, on) {
    if (stateEl) stateEl.textContent = text;
    if (btn) btn.textContent = on ? btn.dataset.labelStop : btn.dataset.labelStart;
  }

  function pollFrame() {
    if (!running) return;
    fetch(box.dataset.frameUrl + "?seq=" + seq, { credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 200) {
          failCount = 0;
          seq = parseInt(r.headers.get("X-Frame-Seq") || "0", 10) || seq;
          return r.blob().then(function (b) {
            var url = URL.createObjectURL(b);
            if (img.dataset.prevUrl) URL.revokeObjectURL(img.dataset.prevUrl);
            img.src = url;
            img.dataset.prevUrl = url;
            img.style.display = "";
            setState("LIVE · seq " + seq, true);
          });
        }
        if (r.status === 204) { failCount++; setState(box.dataset.msgWaiting, true); }
        // 304: frame chua doi — giu nguyen
      })
      .catch(function () { failCount++; })
      .finally(function () {
        if (!running) return;
        // camera khong gui frame sau ~30 lan doi -> tu dung
        if (failCount > 30) { stop(); return; }
        timer = setTimeout(pollFrame, 1000);
      });
  }

  function start() {
    fetch(box.dataset.startUrl, {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrf() }
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        running = true;
        seq = 0;
        failCount = 0;
        setState(box.dataset.msgStarting, true);
        pollFrame();
      });
  }

  function stop() {
    running = false;
    if (timer) clearTimeout(timer);
    setState(box.dataset.msgStopped, false);
    fetch(box.dataset.stopUrl, {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrf() }
    });
  }

  if (btn) btn.addEventListener("click", function () {
    if (running) stop(); else start();
  });

  window.addEventListener("beforeunload", function () {
    if (running) navigator.sendBeacon &&
      navigator.sendBeacon(box.dataset.stopUrl, new Blob([], { type: "text/plain" }));
  });
})();
