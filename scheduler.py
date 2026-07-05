from services.realtime_data import get_weather_data


def monitor_hazards():
    data = get_weather_data("Lipa")
    print("[monitor_hazards]", data)
