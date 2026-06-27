/* ============================================================
   DICS AI — app.js
   ============================================================ */

/* ── SOS trigger ─────────────────────────────────────────── */
function triggerSOS() {
    if (confirm('Confirm SOS Emergency Alert? This will immediately notify authorities.')) {
        alert('SOS Alert sent! Emergency responders have been notified.');
    }
}

/* ── Ripple effect on buttons ────────────────────────────── */
function attachRipple(btn) {
    /* Skip the sidebar toggle — it has its own handler in sidebar.html */
    if (btn.id === 'sidebarToggle') return;

    btn.addEventListener('click', function (e) {
        const rect   = btn.getBoundingClientRect();
        const size   = Math.max(rect.width, rect.height) * 1.5;
        const x      = e.clientX - rect.left - size / 2;
        const y      = e.clientY - rect.top  - size / 2;
        const ripple = document.createElement('span');
        ripple.classList.add('dics-ripple');
        ripple.style.cssText = `width:${size}px;height:${size}px;left:${x}px;top:${y}px;`;
        btn.appendChild(ripple);
        ripple.addEventListener('animationend', () => ripple.remove());
    });
}

/* ── Animated stat counters ─────────────────────────────── */
function animateCounter(el) {
    const target   = parseFloat(el.dataset.target || el.textContent.replace(/[^0-9.]/g, ''));
    const suffix   = el.dataset.suffix || '';
    const duration = 900;
    const start    = performance.now();
    if (isNaN(target)) return;
    const isFloat = String(el.dataset.target || el.textContent).includes('.');
    (function step(now) {
        const progress = Math.min((now - start) / duration, 1);
        const eased    = 1 - Math.pow(1 - progress, 3);
        const value    = target * eased;
        el.textContent = (isFloat ? value.toFixed(1) : Math.round(value)) + suffix;
        if (progress < 1) requestAnimationFrame(step);
    })(start);
}

/* ── Scroll reveal for cards ─────────────────────────────── */
function initScrollReveal() {
    const io = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.animationPlayState = 'running';
                io.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1 });
    document.querySelectorAll('.card-custom, .stat-card').forEach(el => {
        el.style.animationPlayState = 'paused';
        io.observe(el);
    });
}

/* ── Navbar scroll shadow ────────────────────────────────── */
function initNavbarScroll() {
    const navbar = document.querySelector('.navbar-custom');
    if (!navbar) return;
    window.addEventListener('scroll', () => {
        navbar.style.boxShadow      = window.scrollY > 8
            ? '0 4px 30px rgba(0,0,0,0.7)'
            : '0 4px 30px rgba(0,0,0,0.5)';
        navbar.style.backdropFilter = window.scrollY > 8 ? 'blur(10px)' : '';
    }, { passive: true });
}

/* ── Flash-message auto-dismiss ─────────────────────────── */
function initAutoFlash() {
    document.querySelectorAll('.alert-dismissible.fade.show').forEach(alert => {
        setTimeout(() => {
            const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            if (bsAlert) bsAlert.close();
        }, 6000);
    });
}

/* ── Bootstrap form validation ──────────────────────────── */
function initFormValidation() {
    Array.from(document.querySelectorAll('.needs-validation')).forEach(form => {
        Array.from(form.querySelectorAll('input, textarea, select')).forEach(field => {
            field.addEventListener('blur',  () => form.classList.add('was-validated'));
            field.addEventListener('input', () => {
                if (form.classList.contains('was-validated')) {
                    field.classList.toggle('is-valid',   field.checkValidity());
                    field.classList.toggle('is-invalid', !field.checkValidity());
                }
            });
        });
        form.addEventListener('submit', e => {
            if (!form.checkValidity()) { e.preventDefault(); e.stopPropagation(); }
            form.classList.add('was-validated');
        }, false);
    });
}

/* ── Page-exit fade (internal anchor links only) ─────────── */
function initPageTransitions() {
    const IGNORE = ['#', 'javascript:', 'mailto:', 'tel:'];
    document.addEventListener('click', e => {
        /* Only act on real <a href> clicks — buttons are excluded */
        const anchor = e.target.closest('a[href]');
        if (!anchor) return;
        const href = anchor.getAttribute('href') || '';
        const skip = IGNORE.some(p => href.startsWith(p))
            || anchor.target === '_blank'
            || e.ctrlKey || e.metaKey || e.shiftKey;
        if (skip) return;

        e.preventDefault();
        document.body.style.transition = 'opacity 0.18s ease';
        document.body.style.opacity    = '0';
        setTimeout(() => { window.location.href = href; }, 190);
    });
}

/* ── Boot ────────────────────────────────────────────────── */
window.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.btn').forEach(attachRipple);

    document.querySelectorAll('.stat-value[data-target], .stat-value').forEach(el => {
        if (/^\d/.test(el.textContent.trim())) animateCounter(el);
    });

    initScrollReveal();
    initNavbarScroll();
    initAutoFlash();
    initFormValidation();
    initPageTransitions();

    /* NOTE: Sidebar toggle is handled entirely inside sidebar.html's own
       <script> block. Do NOT add sidebar logic here to avoid double-binding. */

    /* Fade page in on load */
    document.body.style.opacity    = '0';
    document.body.style.transition = 'opacity 0.25s ease';
    requestAnimationFrame(() => {
        requestAnimationFrame(() => { document.body.style.opacity = '1'; });
    });
});

/* ── Back/Forward cache restore fix ─────────────────────────
   The page-exit fade sets opacity:0 before navigating away.
   When the browser restores the page from bfcache (back button),
   DOMContentLoaded does NOT re-fire — so the body stays invisible.
   pageshow fires on every restore, including bfcache hits. ── */
window.addEventListener('pageshow', function (e) {
    if (e.persisted) {
        /* Page was restored from back/forward cache */
        document.body.style.transition = 'opacity 0.2s ease';
        document.body.style.opacity    = '1';
    }
});