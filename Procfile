web: cd fincorya && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120 --access-logfile - --error-logfile -
release: cd fincorya && python manage.py migrate --noinput && python manage.py create_admin
