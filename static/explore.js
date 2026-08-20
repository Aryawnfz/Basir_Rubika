(function () {
  "use strict";

  const grid = document.getElementById("exploreGrid");
  const emptyMsg = document.getElementById("exploreEmpty");
  const addForm = document.getElementById("exploreAddForm");
  const addInput = document.getElementById("channelInput");
  const addBtn = document.getElementById("channelAddBtn");
  const addError = document.getElementById("channelAddError");

  const bulkNum = document.getElementById("bulkNum");
  const bulkInc = document.getElementById("bulkInc");
  const bulkDec = document.getElementById("bulkDec");
  const analyzeAllBtn = document.getElementById("analyzeAllBtn");
  const refreshAllBtn = document.getElementById("refreshAllBtn");
  const deleteAllBtn = document.getElementById("deleteAllBtn");
  const exportAllBtn = document.getElementById("exportAllBtn");
  const toolbarStatus = document.getElementById("toolbarStatus");

  const modal = document.getElementById("statsModal");
  const modalTitle = document.getElementById("modalTitle");
  const modalClose = document.getElementById("modalClose");
  const modalLoading = document.getElementById("modalLoading");
  const modalError = document.getElementById("modalError");
  const statsGrid = document.getElementById("statsGrid");
  const exportBtn = document.getElementById("modalExportBtn");

  let pollTimer = null;

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function fmt(n) {
    if (n == null) return "—";
    return Number(n).toLocaleString("fa-IR");
  }

  function refreshEmpty() {
    const has = grid.querySelectorAll(".channel-card").length > 0;
    emptyMsg.style.display = has ? "none" : "";
  }

  function buildCard(c) {
    const avatar = c.avatar_url
      ? `<img src="${esc(c.avatar_url)}" alt="${esc(c.title)}" referrerpolicy="no-referrer" />`
      : `<span class="channel-avatar-letter" style="background-color:${esc(c.avatar_color || "#7c4dff")}">${esc(c.avatar_letter || (c.title || "?").slice(0, 1))}</span>`;
    const bio = c.bio ? `<div class="channel-bio">${esc(c.bio)}</div>` : "";
    const uname = c.username ? `<div class="channel-username">@${esc(c.username)}</div>` : "";
    const members = c.members || ((c.members_count || 0) + " مشترک");
    const el = document.createElement("div");
    el.className = "channel-card";
    el.dataset.id = c.id;
    el.dataset.username = c.username || "";
    el.innerHTML = `
      <button class="channel-remove" title="حذف از لیست" aria-label="حذف">&times;</button>
      <div class="channel-avatar">${avatar}</div>
      <div class="channel-title">${esc(c.title)}</div>
      ${uname}
      ${bio}
      <div class="channel-members">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/></svg>
        <span>${esc(members)}</span>
      </div>
      <div class="channel-status"></div>
      <button type="button" class="channel-show-btn" style="display:none">نمایش</button>`;
    return el;
  }

  // ── افزودن کانال (چندتایی؛ هر خط یک لینک/آیدی) ─────────────────────
  addForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const raw = addInput.value.trim();
    if (!raw) return;
    const lines = raw.split("\n").map((s) => s.trim()).filter(Boolean);
    addError.textContent = "";
    addError.className = "explore-add-error";
    addBtn.disabled = true;
    addBtn.textContent = lines.length > 1 ? "در حال دریافت کانال‌ها…" : "در حال دریافت…";
    try {
      const fd = new FormData();
      fd.append("channels", raw);
      const res = await fetch("/explore/add_bulk", { method: "POST", body: fd });
      const data = await res.json();
      if (!data.ok) {
        addError.textContent = data.error || "خطا در افزودن کانال‌ها.";
        return;
      }
      (data.items || []).forEach((it) => {
        if (it.ok && it.channel) grid.prepend(buildCard(it.channel));
      });
      refreshEmpty();
      const parts = [];
      if (data.added) parts.push(`${data.added} کانال اضافه شد`);
      if (data.duplicate) parts.push(`${data.duplicate} تکراری`);
      if (data.failed) parts.push(`${data.failed} ناموفق`);
      addError.textContent = parts.join(" • ");
      addError.className = "explore-add-error" + (data.failed ? "" : " ok");
      if (data.added && !data.failed) addInput.value = "";
    } catch (err) {
      addError.textContent = "خطای ارتباط با سرور.";
    } finally {
      addBtn.disabled = false;
      addBtn.textContent = "افزودن";
    }
  });

  // ── حذف کارت / دکمهٔ نمایش (delegation) ───────────────────────────
  grid.addEventListener("click", async function (e) {
    const card = e.target.closest(".channel-card");
    if (!card) return;

    if (e.target.closest(".channel-remove")) {
      const sure = await window.showConfirm(
        "حذف کانال",
        "این کانال از لیست حذف شود؟"
      );
      if (!sure) return;
      const id = card.dataset.id;
      const res = await fetch("/explore/delete/" + id, { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        card.remove();
        refreshEmpty();
      }
      return;
    }

    if (e.target.closest(".channel-show-btn")) {
      showChannelModal(card);
    }
  });

  // ── تأییدِ سبکِ SweetAlert (تعریف‌شده به‌صورت سراسری در confirm-modal.js) ──
  const showConfirm = window.showConfirm;

  // ── حذفِ همهٔ کانال‌ها ───────────────────────────────────────────
  deleteAllBtn.addEventListener("click", async function () {
    if (grid.querySelectorAll(".channel-card").length === 0) return;
    const sure = await showConfirm(
      "حذف تمامی کانال‌ها",
      "آیا از حذف تمامی کانال‌ها اطمینان دارید؟ این عملیات قابل بازگشت نیست."
    );
    if (!sure) return;
    deleteAllBtn.disabled = true;
    try {
      const res = await fetch("/explore/delete_all", { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        grid.innerHTML = "";
        refreshEmpty();
        exportAllBtn.style.display = "none";
        toolbarStatus.textContent = `${data.count} کانال حذف شد.`;
        toolbarStatus.className = "explore-toolbar-status";
      } else {
        toolbarStatus.textContent = data.error || "خطا در حذفِ کانال‌ها.";
        toolbarStatus.className = "explore-toolbar-status error";
      }
    } catch (err) {
      toolbarStatus.textContent = "خطای ارتباط با سرور.";
      toolbarStatus.className = "explore-toolbar-status error";
    } finally {
      deleteAllBtn.disabled = false;
    }
  });

  // ── به‌روزرسانیِ اطلاعاتِ همهٔ کانال‌ها ──────────────────────────
  refreshAllBtn.addEventListener("click", async function () {
    if (grid.querySelectorAll(".channel-card").length === 0) return;
    refreshAllBtn.disabled = true;
    refreshAllBtn.textContent = "در حال به‌روزرسانی…";
    toolbarStatus.textContent = "در حال دریافتِ اطلاعاتِ تازهٔ کانال‌ها…";
    toolbarStatus.className = "explore-toolbar-status running";
    try {
      const res = await fetch("/explore/refresh_all", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        toolbarStatus.textContent = data.error || "خطا در به‌روزرسانیِ اطلاعات.";
        toolbarStatus.className = "explore-toolbar-status error";
        return;
      }
      (data.items || []).forEach((it) => {
        if (it.ok && it.channel) {
          const card = grid.querySelector(`.channel-card[data-id="${it.id}"]`);
          if (card) card.replaceWith(buildCard(it.channel));
        }
      });
      loadStatus(); // وضعیتِ بررسیِ قبلی (در صورت وجود) را روی کارت‌های تازه‌ساز برگردان
      toolbarStatus.textContent = `${data.updated} کانال به‌روزرسانی شد`
        + (data.failed ? ` • ${data.failed} ناموفق` : "");
      toolbarStatus.className = "explore-toolbar-status" + (data.failed ? " error" : "");
    } catch (err) {
      toolbarStatus.textContent = "خطای ارتباط با سرور.";
      toolbarStatus.className = "explore-toolbar-status error";
    } finally {
      refreshAllBtn.disabled = false;
      refreshAllBtn.textContent = "به‌روزرسانی اطلاعات";
    }
  });

  // ── انتخابگرِ عدد ─────────────────────────────────────────────────
  function clampNum() {
    let v = parseInt(bulkNum.value, 10) || 1;
    bulkNum.value = Math.max(1, Math.min(50, v));
  }
  bulkInc.addEventListener("click", () => { bulkNum.value = Math.min(50, (parseInt(bulkNum.value, 10) || 1) + 1); });
  bulkDec.addEventListener("click", () => { bulkNum.value = Math.max(1, (parseInt(bulkNum.value, 10) || 1) - 1); });
  bulkNum.addEventListener("input", clampNum);

  // ── بررسی همه ─────────────────────────────────────────────────────
  analyzeAllBtn.addEventListener("click", async function () {
    clampNum();
    const n = parseInt(bulkNum.value, 10) || 10;
    if (grid.querySelectorAll(".channel-card").length === 0) {
      toolbarStatus.textContent = "ابتدا حداقل یک کانال اضافه کنید.";
      toolbarStatus.className = "explore-toolbar-status error";
      return;
    }
    analyzeAllBtn.disabled = true;
    analyzeAllBtn.textContent = "در حال شروع…";
    try {
      const fd = new FormData();
      fd.append("num_posts", n);
      const res = await fetch("/explore/analyze", { method: "POST", body: fd });
      const data = await res.json();
      if (!data.ok) {
        toolbarStatus.textContent = data.error || "خطا در شروع بررسی.";
        toolbarStatus.className = "explore-toolbar-status error";
        return;
      }
      loadStatus();
    } catch (err) {
      toolbarStatus.textContent = "خطای ارتباط با سرور.";
      toolbarStatus.className = "explore-toolbar-status error";
    } finally {
      analyzeAllBtn.disabled = false;
      analyzeAllBtn.textContent = "بررسی همه";
    }
  });

  // ── وضعیتِ سراسری (ماندگار؛ برای همه یکسان) ───────────────────────
  function applyCardStatus(card, st) {
    const sEl = card.querySelector(".channel-status");
    const showBtn = card.querySelector(".channel-show-btn");
    if (!sEl || !showBtn) return;
    if (!st) {
      sEl.textContent = "";
      sEl.className = "channel-status";
      showBtn.style.display = "none";
      return;
    }
    if (st.status === "queued" || st.status === "running") {
      sEl.textContent = st.status === "queued"
        ? "در صفِ بررسی…"
        : `در حال بررسیِ ${fmt(st.num_posts)} پست آخر…`;
      sEl.className = "channel-status running";
      showBtn.style.display = "none";
    } else if (st.status === "done") {
      const s = st.summary || {};
      sEl.textContent = `آماده — ${fmt(s.posts_analyzed)} پست بررسی شد`;
      sEl.className = "channel-status done";
      showBtn.style.display = "";
    } else if (st.status === "error") {
      sEl.textContent = st.error || "خطا در بررسی.";
      sEl.className = "channel-status error";
      showBtn.style.display = "none";
    }
  }

  async function loadStatus() {
    let data;
    try {
      const res = await fetch("/explore/status");
      data = await res.json();
    } catch (err) {
      return;
    }
    if (!data || !data.ok) return;
    const map = data.channels || {};
    let anyRunning = false;
    let anyDone = false;
    grid.querySelectorAll(".channel-card").forEach((card) => {
      const st = map[card.dataset.id];
      applyCardStatus(card, st);
      if (st && (st.status === "queued" || st.status === "running")) anyRunning = true;
      if (st && st.status === "done") anyDone = true;
    });
    exportAllBtn.style.display = anyDone ? "" : "none";
    if (anyRunning) {
      toolbarStatus.textContent = "بررسی در حال انجام است… (این وضعیت برای همهٔ کاربران یکسان است)";
      toolbarStatus.className = "explore-toolbar-status running";
    } else {
      toolbarStatus.textContent = "";
      toolbarStatus.className = "explore-toolbar-status";
    }
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (anyRunning) pollTimer = setTimeout(loadStatus, 2500);
  }

  // ── مودالِ نمایشِ نتیجه ─────────────────────────────────────────
  async function showChannelModal(card) {
    const title = card.querySelector(".channel-title").textContent;
    modalTitle.textContent = "آمار کانال — " + title;
    modalLoading.style.display = "";
    modalError.style.display = "none";
    modalError.textContent = "";
    statsGrid.style.display = "none";
    exportBtn.style.display = "none";
    modal.style.display = "flex";
    try {
      const res = await fetch("/explore/analysis/" + card.dataset.id);
      const data = await res.json();
      modalLoading.style.display = "none";
      if (!data.ok || data.status !== "done" || !data.result) {
        modalError.style.display = "";
        modalError.textContent = data.error || "آمار هنوز آماده نیست.";
        return;
      }
      renderStats(card.dataset.id, data.result);
    } catch (err) {
      modalLoading.style.display = "none";
      modalError.style.display = "";
      modalError.textContent = "خطای ارتباط با سرور.";
    }
  }

  function closeModal() { modal.style.display = "none"; }
  modalClose.addEventListener("click", closeModal);
  modal.addEventListener("click", function (e) { if (e.target === modal) closeModal(); });

  function renderStats(channelId, r) {
    if (!r || r.posts_analyzed === 0) {
      modalError.style.display = "";
      modalError.textContent = "هیچ پست کاملی برای این کانال پیدا نشد.";
      return;
    }
    document.getElementById("stPosts").textContent = fmt(r.posts_analyzed);
    document.getElementById("stViews").textContent = fmt(r.total_views);
    document.getElementById("stReactions").textContent = fmt(r.total_reactions);
    document.getElementById("stAvgViews").textContent = fmt(r.avg_views);
    document.getElementById("stAvgReactions").textContent = fmt(r.avg_reactions);
    document.getElementById("stEngagement").textContent = fmt(r.engagement_rate);
    statsGrid.style.display = "";
    exportBtn.href = "/explore/export/" + channelId;
    exportBtn.style.display = "";
  }

  refreshEmpty();
  loadStatus();
})();
