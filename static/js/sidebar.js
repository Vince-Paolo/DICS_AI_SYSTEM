/* Sidebar toggle — this is the ONLY place this logic lives.
   app.js intentionally excludes sidebar handling to avoid double-binding. */
(function () {
    function initSidebar() {
        const toggle    = document.getElementById('sidebarToggle');
        const container = document.querySelector('.sidebar-container');
        if (!toggle || !container) return;

        function setCollapsed(collapsed) {
            container.classList.toggle('collapsed', collapsed);
            document.body.classList.toggle('sidebar-collapsed', collapsed);
            document.documentElement.classList.toggle('sidebar-collapsed', collapsed);

            const icon = toggle.querySelector('i');
            if (icon) {
                icon.className = collapsed ? 'bi bi-list' : 'bi bi-x-lg';
            }

            toggle.setAttribute('aria-expanded', String(!collapsed));
            const label = collapsed ? toggle.dataset.expandLabel : toggle.dataset.collapseLabel;
            toggle.setAttribute('aria-label', label);
            toggle.setAttribute('title', label);
            localStorage.setItem('sidebarCollapsed', collapsed);
        }

        toggle.addEventListener('click', function (e) {
            e.stopPropagation(); /* Prevent bubbling to any global click handlers */
            setCollapsed(!container.classList.contains('collapsed'));
        });

        /* Restore saved state on page load (desktop collapse only) */
        const saved = localStorage.getItem('sidebarCollapsed') === 'true';
        if (saved) setCollapsed(true);
    }

    function initMobileDrawer() {
        const container   = document.getElementById('sidebarContainer');
        const openBtn      = document.getElementById('mobileSidebarToggle');
        const closeBtn      = document.getElementById('sidebarCloseMobile');
        const overlay        = document.getElementById('sidebarOverlay');
        if (!container || !openBtn || !overlay) return;

        function openDrawer() {
            container.classList.add('mobile-open');
            container.inert = false;
            overlay.classList.add('show');
            openBtn.classList.add('is-active');
            openBtn.setAttribute('aria-expanded', 'true');
            openBtn.setAttribute('aria-label', openBtn.dataset.closeLabel);
            container.setAttribute('aria-hidden', 'false');
            overlay.setAttribute('aria-hidden', 'false');
            document.body.style.overflow = 'hidden';
            if (closeBtn) closeBtn.focus();
        }

        function closeDrawer() {
            container.classList.remove('mobile-open');
            container.inert = window.innerWidth <= 991.98;
            overlay.classList.remove('show');
            openBtn.classList.remove('is-active');
            openBtn.setAttribute('aria-expanded', 'false');
            openBtn.setAttribute('aria-label', openBtn.dataset.openLabel);
            container.setAttribute('aria-hidden', 'true');
            overlay.setAttribute('aria-hidden', 'true');
            document.body.style.overflow = '';
            openBtn.focus();
        }

        container.inert = window.innerWidth <= 991.98;
        container.setAttribute('aria-hidden', String(container.inert));

        openBtn.addEventListener('click', openDrawer);
        if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
        overlay.addEventListener('click', closeDrawer);
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && container.classList.contains('mobile-open')) {
                closeDrawer();
            }
        });

        /* Close the drawer whenever a nav link is tapped on mobile */
        container.querySelectorAll('.sidebar-nav-link, .sos-button').forEach(function (el) {
            el.addEventListener('click', function () {
                if (window.innerWidth <= 991.98) closeDrawer();
            });
        });

        /* If the viewport is resized up past the mobile breakpoint while
           the drawer is open, reset state so desktop layout isn't stuck. */
        window.addEventListener('resize', function () {
            if (window.innerWidth > 991.98) {
                if (container.classList.contains('mobile-open')) closeDrawer();
                container.inert = false;
                container.setAttribute('aria-hidden', 'false');
            } else if (!container.classList.contains('mobile-open')) {
                container.inert = true;
                container.setAttribute('aria-hidden', 'true');
            }
        });
    }

    function initBackupLink() {
        const link = document.getElementById('sidebarBackupLink');
        if (!link) return;

        link.addEventListener('click', function (e) {
            // app.js registers a document-level click listener (initPageTransitions)
            // that intercepts every <a href> click for a fade transition, then does
            // a REAL navigation to the href itself. Without stopping propagation here,
            // that listener still fires on this same click and navigates the tab to
            // /admin/backup for real, racing this handler's iframe-based download.
            e.stopPropagation();
            e.preventDefault();
            if (link.dataset.downloading === 'true') return;
            link.dataset.downloading = 'true';

            const textEl = link.querySelector('.nav-text');
            const originalText = textEl ? textEl.textContent : null;
            if (textEl) textEl.textContent = link.dataset.preparingLabel;
            link.style.pointerEvents = 'none';
            link.style.opacity = '0.6';

            window.location.href = link.getAttribute('href');
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initSidebar();
            initMobileDrawer();
            initBackupLink();
        });
    } else {
        initSidebar();
        initMobileDrawer();
        initBackupLink();
    }
})();
