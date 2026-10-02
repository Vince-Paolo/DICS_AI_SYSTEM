web: python -m flask --app app db upgrade heads && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
