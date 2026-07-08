from datetime import datetime, timedelta

from services.realtime_data import get_weather_data
from ai.prediction import predict_hazard
from models import db, Incident


def monitor_hazards():
    from app import app

    with app.app_context():
        weather_data = get_weather_data("Lipa")
        if not weather_data:
            app.logger.info("Hazard monitoring skipped: no weather data available")
            return

        city = weather_data.get("city") or "Lipa"
        rainfall_mm = float(weather_data.get("rainfall", 0) or 0)
        humidity_pct = float(weather_data.get("humidity", 0) or 0)
        river_level_m = 0.0
        population_density = 1000

        hazard_configs = [
            {
                "hazard_type": "flood",
                "rainfall_mm": rainfall_mm,
                "river_level_m": river_level_m,
                "soil_moisture_pct": humidity_pct,
                "population_density": population_density,
            },
            {
                "hazard_type": "landslide",
                "rainfall_mm": rainfall_mm,
                "river_level_m": river_level_m,
                "soil_moisture_pct": humidity_pct,
                "population_density": population_density,
            },
        ]

        created_any = False
        for config in hazard_configs:
            try:
                prediction = predict_hazard(**config)
            except Exception as exc:
                app.logger.warning("Hazard monitoring: failed to predict %s: %s", config["hazard_type"], exc)
                continue

            if not prediction:
                continue

            threshold = 50.0
            if prediction.get("score", 0) < threshold:
                app.logger.info(
                    "Hazard monitoring: %s score %.1f below threshold %.1f",
                    config["hazard_type"],
                    prediction.get("score", 0),
                    threshold,
                )
                continue

            recent_incident = Incident.query.filter_by(
                hazard_type=prediction.get("type", config["hazard_type"]),
                location=city,
                alert=True,
            ).filter(Incident.created_at >= datetime.utcnow() - timedelta(hours=6)).order_by(Incident.created_at.desc()).first()

            if recent_incident:
                app.logger.info(
                    "Hazard monitoring: recent alert already exists for %s in %s",
                    prediction.get("type", config["hazard_type"]),
                    city,
                )
                continue

            incident = Incident(
                hazard_type=prediction.get("type", config["hazard_type"]),
                location=city,
                rainfall_mm=rainfall_mm,
                river_level_m=river_level_m,
                soil_moisture_pct=humidity_pct,
                population_density=population_density,
                score=float(prediction.get("score", 0) or 0),
                level=prediction.get("level", "Moderate"),
                message=prediction.get("message", "High hazard risk detected."),
                alert=bool(prediction.get("alert", False)),
                status='ACTIVE' if prediction.get("alert") else 'NEW',
                reported_by='system',
            )
            db.session.add(incident)
            created_any = True

        if created_any:
            db.session.commit()
            app.logger.info("Created hazard incidents for monitored hazards in %s", city)
        else:
            db.session.rollback()
            app.logger.info("Hazard monitoring: no high-risk incidents created")
