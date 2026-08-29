/* ============================================================
   DICS AI — app.js
   ============================================================ */

/* ── SOS trigger ─────────────────────────────────────────── */
function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function showToast(message, type = 'info', title = '') {
    const container = document.getElementById('appToastContainer');
    if (!container) return;

    const variant = type === 'success' ? 'success' : type === 'danger' ? 'danger' : type === 'warning' ? 'warning' : 'info';
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-bg-${variant} border-0`;
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${title ? `<strong class="d-block mb-1">${escapeHtml(title)}</strong>` : ''}
                ${escapeHtml(message)}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>`;

    container.appendChild(toast);
    const instance = new bootstrap.Toast(toast, { autohide: true, delay: 5000 });
    instance.show();
    toast.addEventListener('hidden.bs.toast', () => toast.remove());
}

function requestConfirmation({ title = 'Confirm action', message = 'Are you sure you want to continue?', confirmLabel = 'Continue' } = {}) {
    return new Promise(resolve => {
        const modalEl = document.getElementById('globalConfirmModal');
        const titleEl = document.getElementById('globalConfirmTitle');
        const bodyEl = document.getElementById('globalConfirmBody');
        const confirmBtn = document.getElementById('globalConfirmAction');
        const cancelBtn = document.getElementById('globalConfirmCancel');
        if (!modalEl || !titleEl || !bodyEl || !confirmBtn || !cancelBtn) {
            resolve(false);
            return;
        }

        titleEl.textContent = title;
        bodyEl.textContent = message;
        confirmBtn.textContent = confirmLabel;

        const cleanup = () => {
            confirmBtn.onclick = null;
            cancelBtn.onclick = null;
            modalEl.removeEventListener('hidden.bs.modal', onHidden);
        };

        const onHidden = () => {
            cleanup();
            resolve(false);
        };

        modalEl.addEventListener('hidden.bs.modal', onHidden, { once: true });
        confirmBtn.onclick = () => {
            cleanup();
            modalEl.querySelector('.btn-close').click();
            resolve(true);
        };
        cancelBtn.onclick = () => {
            cleanup();
            modalEl.querySelector('.btn-close').click();
            resolve(false);
        };

        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    });
}

function initAccessibleConfirmations() {
    document.querySelectorAll('[data-confirm]').forEach(element => {
        if (element.dataset.confirmBound === 'true') return;
        element.dataset.confirmBound = 'true';

        element.addEventListener('click', async event => {
            if (event.defaultPrevented) return;
            const title = element.dataset.confirmTitle || 'Confirm action';
            const message = element.dataset.confirm || 'Are you sure you want to continue?';
            const confirmed = await requestConfirmation({ title, message });
            if (!confirmed) {
                event.preventDefault();
                event.stopPropagation();
                return;
            }

            const form = element.closest('form');
            if (form) {
                event.preventDefault();
                form.submit();
            }
        });
    });
}

async function triggerSOS() {
    const confirmed = await requestConfirmation({
        title: 'Send SOS alert',
        message: 'This will immediately notify emergency authorities and services. Continue?',
        confirmLabel: 'Send alert'
    });
    if (!confirmed) {
        return;
    }

    const sosButton = document.querySelector('.sos-button');
    if (sosButton) {
        sosButton.disabled = true;
    }

    try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const response = await fetch('/emergency-sos', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify({
                location: 'User Emergency Location'
            })
        });
        const data = await response.json();
        if (data.success) {
            showToast(data.message || 'Emergency alert sent successfully.', 'success', 'SOS alert sent');
        } else {
            showToast(data.message || 'Unable to send emergency alert.', 'danger', 'Alert failed');
        }
    } catch (error) {
        console.error('Error:', error);
        showToast('Unable to send emergency alert. Please contact authorities directly.', 'danger', 'Alert failed');
    } finally {
        if (sosButton) {
            sosButton.disabled = false;
        }
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

/* ── Reduced motion preference ──────────────────────────────
   CSS already kills transitions/animations via prefers-reduced-motion,
   but these two effects are driven by JS (opacity set via inline style,
   and a requestAnimationFrame counter loop) so they need their own check. */
function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/* ── Animated stat counters ─────────────────────────────── */
function animateCounter(el) {
    const target   = parseFloat(el.dataset.target || el.textContent.replace(/[^0-9.]/g, ''));
    const suffix   = el.dataset.suffix || '';
    if (isNaN(target)) return;
    const isFloat = String(el.dataset.target || el.textContent).includes('.');
    if (prefersReducedMotion()) {
        el.textContent = (isFloat ? target.toFixed(1) : Math.round(target)) + suffix;
        return;
    }
    const duration = 900;
    const start    = performance.now();
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
        if (prefersReducedMotion()) {
            window.location.href = href;
            return;
        }
        document.body.style.transition = 'opacity 0.18s ease';
        document.body.style.opacity    = '0';
        setTimeout(() => { window.location.href = href; }, 190);
    });
}

/* ── Boot ────────────────────────────────────────────────── */
window.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.btn').forEach(attachRipple);
    initAccessibleConfirmations();

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
    if (!prefersReducedMotion()) {
        document.body.style.opacity    = '0';
        document.body.style.transition = 'opacity 0.25s ease';
        requestAnimationFrame(() => {
            requestAnimationFrame(() => { document.body.style.opacity = '1'; });
        });
    }
});

/* ── Back/Forward cache restore fix ─────────────────────────
   The page-exit fade sets opacity:0 before navigating away.
   When the browser restores the page from bfcache (back button),
   DOMContentLoaded does NOT re-fire — so the body stays invisible.
   pageshow fires on every restore, including bfcache hits. ── */
window.addEventListener('pageshow', function (e) {
    if (e.persisted) {
        /* Page was restored from back/forward cache */
        if (prefersReducedMotion()) {
            document.body.style.opacity = '1';
            return;
        }
        document.body.style.transition = 'opacity 0.2s ease';
        document.body.style.opacity    = '1';
    }
});