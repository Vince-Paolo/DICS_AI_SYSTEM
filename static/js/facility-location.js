document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('addFacilityModal');
    if (!modal) return;
    const barangay = document.getElementById('facility_barangay_id');
    const latitude = document.getElementById('latitude');
    const longitude = document.getElementById('longitude');
    const hint = document.getElementById('facility-location-hint');
    const coordinates = JSON.parse(document.getElementById('facility-barangay-coordinates').textContent);
    let map;
    let marker;

    function placePin(lat, lng, message) {
        latitude.value = lat.toFixed(6);
        longitude.value = lng.toFixed(6);
        hint.textContent = message;
        if (!map) return;
        if (!marker) {
            marker = L.marker([lat, lng], {draggable: true}).addTo(map);
            marker.on('dragend', () => {
                const position = marker.getLatLng();
                placePin(position.lat, position.lng, 'Pin adjusted to the facility location.');
            });
        } else {
            marker.setLatLng([lat, lng]);
        }
        map.setView([lat, lng], Math.max(map.getZoom(), 16));
    }

    function clearLocation() {
        latitude.value = '';
        longitude.value = '';
        if (marker) marker.remove();
        marker = null;
        hint.textContent = 'Select a barangay to start near its center, then click the map or drag the pin to the facility.';
        if (map) map.setView([14.0693, 121.3256], 13);
    }

    barangay.addEventListener('facility:location-reset', clearLocation);
    barangay.addEventListener('change', () => {
        clearLocation();
        const point = coordinates[barangay.value];
        if (point) {
            placePin(point.lat, point.lng, 'Approximate barangay center only. Move the pin to the facility’s exact location before saving.');
        } else if (barangay.value) {
            hint.textContent = 'No starting coordinates for this barangay. Click the map or enter the facility coordinates below.';
        }
    });

    function syncCoordinates() {
        const lat = Number.parseFloat(latitude.value);
        const lng = Number.parseFloat(longitude.value);
        const valid = Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180;
        if (valid) {
            placePin(lat, lng, 'Location set from the coordinates below.');
        } else if (marker) {
            marker.remove();
            marker = null;
        }
    }
    latitude.addEventListener('change', syncCoordinates);
    longitude.addEventListener('change', syncCoordinates);

    modal.addEventListener('shown.bs.modal', () => {
        if (typeof L === 'undefined') {
            hint.textContent = 'Map unavailable. Select a barangay to prefill coordinates, or enter them below.';
            return;
        }
        if (!map) {
            map = L.map('facility-location-map').setView([14.0693, 121.3256], 13);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            }).addTo(map);
            map.on('click', (event) => placePin(event.latlng.lat, event.latlng.lng, 'Pin placed at the facility location.'));
            if (latitude.value && longitude.value) syncCoordinates();
        }
        map.invalidateSize();
    });
});
