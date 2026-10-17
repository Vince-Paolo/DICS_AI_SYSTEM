(() => {
    const page = document.querySelector('.landing-page');
    if (!page || !('IntersectionObserver' in window) ||
        window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    // Content stays visible if scripts or browser animation support are unavailable.
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('landing-reveal-enter');
            observer.unobserve(entry.target);
        });
    }, { threshold: 0.15 });

    page.querySelectorAll('.landing-visual, .landing-capability').forEach((element) => {
        observer.observe(element);
    });
})();
