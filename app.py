    for incident in incidents:
        report = None
        incident_report_id = getattr(incident, 'citizen_report_id', None)
        if incident_report_id is not None:
            report = incident.citizen_report or report_map.get(incident_report_id)
        elif getattr(incident, 'user_id', None) is not None:
            report = next(
                (
                    candidate_report
                    for candidate_report in report_map.values()
                    if candidate_report.user_id == incident.user_id
                ),
                None,
            )

        latitude = incident.latitude
        longitude = incident.longitude
        if latitude is None or longitude is None:
            latitude = report.gps_latitude if report else None
            longitude = report.gps_longitude if report else None
        if latitude is None or longitude is None:
            continue
