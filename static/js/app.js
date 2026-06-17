function triggerSOS() {
    if (confirm('Confirm SOS Emergency Alert? This will immediately notify authorities.')) {
        alert('SOS Alert sent! Emergency responders have been notified.');
    }
}

window.addEventListener('DOMContentLoaded', function () {
    const forms = Array.from(document.querySelectorAll('.needs-validation'));
    forms.forEach(function (form) {
        const fields = Array.from(form.querySelectorAll('input, textarea, select'));
        fields.forEach(function (field) {
            field.addEventListener('blur', function () {
                form.classList.add('was-validated');
            });
            field.addEventListener('input', function () {
                if (form.classList.contains('was-validated')) {
                    field.classList.toggle('is-valid', field.checkValidity());
                    field.classList.toggle('is-invalid', !field.checkValidity());
                }
            });
        });

        form.addEventListener('submit', function (event) {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        }, false);
    });
});
