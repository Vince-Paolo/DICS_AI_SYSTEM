import time
import schedule
from services.realtime_data import get_weather_data


def monitor_hazards():
    data = get_weather_data("Lipa")
    print("[monitor_hazards]", data)


if __name__ == '__main__':
    schedule.every(5).minutes.do(monitor_hazards)
    print("Starting hazard monitor (ctrl-c to stop)...")
    while True:
        schedule.run_pending()
        time.sleep(1)
