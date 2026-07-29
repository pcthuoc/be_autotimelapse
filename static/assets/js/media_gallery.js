/*
 * media_gallery.js — tương tác trang xem/tải ảnh của 1 camera.
 *
 * Thiết kế cho khối lượng ảnh lớn:
 *   - Phân trang 1..N nạp bằng AJAX (swap #galleryBody) + spinner xoay tròn.
 *   - Click ảnh mở lightbox modal (không chuyển trang) + prev/next.
 *   - Gom tải: POST tạo job nén nền → poll trạng thái → khi "Sẵn sàng tải"
 *     hiện thông báo (Notyf) + link tải (URL có vòng đời/hết hạn).
 */
(function () {
  "use strict";

  var cfg = document.getElementById("galleryConfig");
  if (!cfg) return;

  var CAMERA = cfg.dataset.camera;
  var URL_CREATE = cfg.dataset.archiveCreate;
  var URL_LIST = cfg.dataset.archiveList;
  var URL_GALLERY = cfg.dataset.galleryUrl;
  var CAN_DL = cfg.dataset.canDownload === "1";

  var notyf = (typeof Notyf !== "undefined")
    ? new Notyf({ duration: 5000, position: { x: "right", y: "top" }, dismissible: true })
    : null;

  function toast(msg, type) {
    if (!notyf) { return; }
    if (type === "error") { notyf.error(msg); }
    else if (type === "warning") { notyf.open({ type: "warning", background: "#f0ad4e", message: msg }); }
    else { notyf.success(msg); }
  }

  function csrf() {
    var el = document.querySelector("#csrfHolder input[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  // ── Phân trang AJAX ─────────────────────────────────────────────────────
  var wrap = document.getElementById("galleryWrap");
  var body = document.getElementById("galleryBody");
  var spinner = document.getElementById("gallerySpinner");

  function currentDate() {
    var d = document.getElementById("dateJump");
    return d && d.value ? d.value : "";
  }

  function showSpinner(on) {
    if (!spinner) { return; }
    spinner.classList.toggle("d-none", !on);
    spinner.classList.toggle("d-flex", on);
  }

  function loadPage(page) {
    var url = URL_GALLERY + "?page=" + encodeURIComponent(page);
    var date = currentDate();
    if (date) { url += "&date=" + encodeURIComponent(date); }
    showSpinner(true);
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        body.innerHTML = html;
        showSpinner(false);
        resetSelection();
        wrap.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch(function () {
        showSpinner(false);
        toast("Không tải được trang.", "error");
      });
  }

  // Delegation: click số trang
  document.addEventListener("click", function (e) {
    var a = e.target.closest(".js-page");
    if (!a || a.closest(".disabled")) { return; }
    e.preventDefault();
    var p = parseInt(a.getAttribute("data-page"), 10);
    if (p >= 1) { loadPage(p); }
  });

  // ── Nhảy ngày ───────────────────────────────────────────────────────────
  function gotoDate(date) {
    var url = URL_GALLERY + (date ? "?date=" + encodeURIComponent(date) : "");
    window.location.href = url;
  }
  var dateJump = document.getElementById("dateJump");
  if (dateJump) {
    dateJump.addEventListener("change", function () { gotoDate(this.value); });
  }
  var daySelect = document.getElementById("daySelect");
  if (daySelect) {
    daySelect.addEventListener("change", function () { gotoDate(this.value); });
  }

  // ── Lightbox xem ảnh ────────────────────────────────────────────────────
  var imgModalEl = document.getElementById("imgModal");
  var imgModal = imgModalEl && window.bootstrap ? new bootstrap.Modal(imgModalEl) : null;
  var imgEl = document.getElementById("imgModalImg");
  var imgTitle = document.getElementById("imgModalTitle");
  var imgDl = document.getElementById("imgModalDl");
  var imgSpin = document.getElementById("imgSpinner");
  var curIndex = -1;

  function items() { return Array.prototype.slice.call(body.querySelectorAll(".media-item")); }

  function openAt(idx) {
    var list = items();
    if (idx < 0 || idx >= list.length) { return; }
    curIndex = idx;
    var card = list[idx];
    if (imgSpin) { imgSpin.style.display = "block"; }
    if (imgEl) {
      imgEl.style.opacity = "0";
      imgEl.src = card.getAttribute("data-full");
      imgEl.onload = function () {
        imgEl.style.opacity = "1";
        if (imgSpin) { imgSpin.style.display = "none"; }
      };
    }
    if (imgTitle) { imgTitle.textContent = card.getAttribute("data-time") || ""; }
    if (imgDl) { imgDl.setAttribute("href", card.getAttribute("data-dl") || "#"); }
    if (imgModal) { imgModal.show(); }
  }

  document.addEventListener("click", function (e) {
    var thumb = e.target.closest(".media-thumb");
    if (!thumb) { return; }
    var card = thumb.closest(".media-item");
    if (!card) { return; }
    var idx = items().indexOf(card);
    if (idx >= 0) { openAt(idx); }
  });

  var prev = document.getElementById("imgPrev");
  var next = document.getElementById("imgNext");
  if (prev) { prev.addEventListener("click", function () { openAt(curIndex - 1); }); }
  if (next) { next.addEventListener("click", function () { openAt(curIndex + 1); }); }
  document.addEventListener("keydown", function (e) {
    if (!imgModalEl || !imgModalEl.classList.contains("show")) { return; }
    if (e.key === "ArrowLeft") { openAt(curIndex - 1); }
    else if (e.key === "ArrowRight") { openAt(curIndex + 1); }
  });

  // ── Chọn ảnh ────────────────────────────────────────────────────────────
  var selCount = document.getElementById("selCount");
  var dlSelected = document.getElementById("dlSelected");

  function selectedIds() {
    return Array.prototype.slice
      .call(body.querySelectorAll(".sel-check:checked"))
      .map(function (c) { return c.value; });
  }
  function refreshSelCount() {
    var n = selectedIds().length;
    if (selCount) { selCount.textContent = n; }
    if (dlSelected) { dlSelected.disabled = n === 0; }
  }
  function resetSelection() { refreshSelCount(); }

  document.addEventListener("change", function (e) {
    if (e.target.classList.contains("sel-check")) { refreshSelCount(); }
  });
  var selAll = document.getElementById("selAll");
  if (selAll) {
    selAll.addEventListener("click", function () {
      body.querySelectorAll(".sel-check").forEach(function (c) { c.checked = true; });
      refreshSelCount();
    });
  }
  var selClear = document.getElementById("selClear");
  if (selClear) {
    selClear.addEventListener("click", function () {
      body.querySelectorAll(".sel-check").forEach(function (c) { c.checked = false; });
      refreshSelCount();
    });
  }

  // ── Gom tải: tạo job + poll ─────────────────────────────────────────────
  var polling = {};   // archiveId -> intervalId

  function createArchive(payload, label) {
    var fd = new FormData();
    fd.append("csrfmiddlewaretoken", csrf());
    (payload.ids || []).forEach(function (id) { fd.append("ids", id); });
    if (payload.date_from) { fd.append("date_from", payload.date_from); }
    if (payload.date_to) { fd.append("date_to", payload.date_to); }
    if (payload.day) { fd.append("day", payload.day); }

    toast("Đang tạo gói tải" + (label ? " (" + label + ")" : "") + "…");
    fetch(URL_CREATE, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: fd
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.j.ok) {
          toast(res.j.error || "Không tạo được gói tải.", "error");
          return;
        }
        toast("Đã nhận yêu cầu. Đang nén ở nền, sẽ báo khi xong.");
        openDownloadsModal();
        startPolling(res.j.id, res.j.status_url);
      })
      .catch(function () { toast("Lỗi kết nối khi tạo gói tải.", "error"); });
  }

  function startPolling(id, statusUrl) {
    if (polling[id]) { return; }
    polling[id] = setInterval(function () {
      fetch(statusUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json(); })
        .then(function (a) {
          renderArchiveRow(a);
          if (a.status === "ready") {
            stopPolling(id);
            toast("Đã nén xong! Vui lòng tải gói ảnh.", "success");
          } else if (a.status === "failed") {
            stopPolling(id);
            toast("Nén thất bại: " + (a.error || ""), "error");
          } else if (a.status === "expired") {
            stopPolling(id);
          }
        })
        .catch(function () { /* thử lại lần poll sau */ });
    }, 2500);
  }
  function stopPolling(id) {
    if (polling[id]) { clearInterval(polling[id]); delete polling[id]; }
  }

  // Nút tải
  if (dlSelected) {
    dlSelected.addEventListener("click", function () {
      var ids = selectedIds();
      if (!ids.length) { return; }
      createArchive({ ids: ids }, ids.length + " ảnh");
    });
  }
  var dlDay = document.getElementById("dlDay");
  if (dlDay) {
    dlDay.addEventListener("click", function () {
      createArchive({ day: this.getAttribute("data-day") }, "cả ngày");
    });
  }
  var dlRange = document.getElementById("dlRange");
  if (dlRange) {
    dlRange.addEventListener("click", function () {
      var from = document.getElementById("dtFrom").value;
      var to = document.getElementById("dtTo").value;
      if (!from && !to) { toast("Chọn khoảng thời gian trước.", "warning"); return; }
      createArchive({ date_from: from, date_to: to }, "theo khoảng");
    });
  }

  // ── Modal danh sách gói tải ─────────────────────────────────────────────
  var dlList = document.getElementById("dlList");

  function fmtSize(b) {
    if (!b) { return "—"; }
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(1) + " " + u[i];
  }
  function statusBadge(a) {
    var map = {
      pending: "secondary", processing: "info",
      ready: "success", failed: "danger", expired: "dark"
    };
    return '<span class="badge bg-' + (map[a.status] || "secondary") + '">'
      + (a.status_display || a.status) + "</span>";
  }
  function archiveRowHtml(a) {
    var right;
    if (a.status === "ready" && a.download_url) {
      right = '<a class="btn btn-sm btn-primary" href="' + a.download_url + '">Tải ZIP</a>';
    } else if (a.status === "processing" || a.status === "pending") {
      right = '<div class="spinner-border spinner-border-sm text-primary"></div>';
    } else {
      right = "";
    }
    var meta = a.item_count + " ảnh · " + fmtSize(a.size_bytes);
    if (a.expires_at && a.status === "ready") {
      meta += " · hết hạn: " + new Date(a.expires_at).toLocaleString("vi-VN");
    }
    if (a.status === "failed" && a.error) { meta += " · " + a.error; }
    return '<div class="d-flex align-items-center justify-content-between border rounded p-2 mb-2" data-arc="' + a.id + '">'
      + '<div class="me-2"><div class="d-flex align-items-center gap-2">' + statusBadge(a)
      + '<span class="small text-muted">' + new Date(a.created_at).toLocaleString("vi-VN") + "</span></div>"
      + '<div class="small">' + meta + "</div></div>"
      + '<div>' + right + "</div></div>";
  }
  function renderArchiveRow(a) {
    if (!dlList) { return; }
    var existing = dlList.querySelector('[data-arc="' + a.id + '"]');
    var tmp = document.createElement("div");
    tmp.innerHTML = archiveRowHtml(a);
    var node = tmp.firstChild;
    if (existing) { existing.replaceWith(node); }
    else {
      var empty = dlList.querySelector(".dl-empty");
      if (empty) { empty.remove(); }
      dlList.insertBefore(node, dlList.firstChild);
    }
  }
  function loadDownloads() {
    if (!dlList) { return; }
    fetch(URL_LIST, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.items.length) {
          dlList.innerHTML = '<div class="text-center text-muted py-3 dl-empty">Chưa có gói tải nào.</div>';
          return;
        }
        dlList.innerHTML = "";
        data.items.forEach(function (a) {
          dlList.appendChild((function () {
            var t = document.createElement("div"); t.innerHTML = archiveRowHtml(a); return t.firstChild;
          })());
          // tiếp tục poll các gói còn đang xử lý (vd. reload trang)
          if (a.status === "pending" || a.status === "processing") {
            startPolling(a.id, a.status_url);
          }
        });
      })
      .catch(function () {
        dlList.innerHTML = '<div class="text-center text-danger py-3">Lỗi tải danh sách.</div>';
      });
  }
  function openDownloadsModal() {
    var el = document.getElementById("dlModal");
    if (el && window.bootstrap) { bootstrap.Modal.getOrCreateInstance(el).show(); }
    loadDownloads();
  }
  var dlModalEl = document.getElementById("dlModal");
  if (dlModalEl) {
    dlModalEl.addEventListener("show.bs.modal", loadDownloads);
  }

  // Khởi tạo
  refreshSelCount();
})();
