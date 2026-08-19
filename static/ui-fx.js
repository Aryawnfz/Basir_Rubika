/* جلوه‌های ظاهری: نورِ دنبال‌کنندهٔ نشانگر روی کارت‌ها. */
(() => {
  const SELECTOR = '.result-card, .channel-card, .account-card, .stat-box, .chart-card, .report-card';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) return;

  document.addEventListener('pointermove', (e) => {
    const card = e.target instanceof Element ? e.target.closest(SELECTOR) : null;
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty('--mx', `${e.clientX - r.left}px`);
    card.style.setProperty('--my', `${e.clientY - r.top}px`);
  }, { passive: true });
})();
