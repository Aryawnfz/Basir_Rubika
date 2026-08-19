/* مودال تأیید سراسری (سبکِ SweetAlert) — جایگزینِ confirm() پیش‌فرض مرورگر
   در تمامِ صفحات. مارک‌آپِ آن در base.html قرار دارد (#confirmModal). */
(function () {
  "use strict";

  const modal = document.getElementById("confirmModal");
  if (!modal) return;

  const iconEl = document.getElementById("confirmIcon");
  const titleEl = document.getElementById("confirmTitle");
  const textEl = document.getElementById("confirmText");
  const okBtn = document.getElementById("confirmOkBtn");
  const cancelBtn = document.getElementById("confirmCancelBtn");

  let activeCleanup = null;

  /**
   * showConfirm(title, text, options?) -> Promise<boolean>
   * options: { okText, cancelText, icon, danger }
   *   danger=false برای عملیاتِ غیرمخرب رنگِ دکمهٔ تأیید را از قرمز به بنفشِ تمِ سایت تغییر می‌دهد.
   */
  window.showConfirm = function showConfirm(title, text, options) {
    const opts = options || {};
    // اگر مودالِ دیگری در حالِ نمایش بود، همان را لغو کن تا تداخل پیش نیاید.
    if (activeCleanup) activeCleanup(false);

    titleEl.textContent = title || "آیا مطمئن هستید؟";
    textEl.textContent = text || "";
    iconEl.textContent = opts.icon || "⚠️";
    okBtn.textContent = opts.okText || "بله، حذف کن";
    cancelBtn.textContent = opts.cancelText || "انصراف";
    okBtn.classList.toggle("confirm-danger", opts.danger !== false);
    okBtn.classList.toggle("confirm-primary", opts.danger === false);

    modal.style.display = "flex";
    requestAnimationFrame(() => modal.classList.add("open"));

    return new Promise((resolve) => {
      function cleanup(result) {
        modal.classList.remove("open");
        setTimeout(() => { modal.style.display = "none"; }, 150);
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        modal.removeEventListener("click", onOverlay);
        document.removeEventListener("keydown", onKey);
        activeCleanup = null;
        resolve(result);
      }
      function onOk() { cleanup(true); }
      function onCancel() { cleanup(false); }
      function onOverlay(e) { if (e.target === modal) cleanup(false); }
      function onKey(e) { if (e.key === "Escape") cleanup(false); }

      activeCleanup = cleanup;
      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      modal.addEventListener("click", onOverlay);
      document.addEventListener("keydown", onKey);
    });
  };

  /**
   * برای فرم‌های حذفِ ساده (اکانت/کاربر): به‌جای onsubmit="return confirm(...)"
   * از onsubmit="return confirmDeleteForm(event, 'عنوان', 'متن')" استفاده کنید.
   */
  window.confirmDeleteForm = function confirmDeleteForm(event, title, text, okText) {
    event.preventDefault();
    const form = event.target;
    window.showConfirm(title, text, { okText: okText || "بله، حذف کن" }).then((ok) => {
      if (ok) form.submit();
    });
    return false;
  };
})();
