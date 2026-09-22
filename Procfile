web: cd fincorya && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120 --access-logfile - --error-logfile -
release: cd fincorya && python manage.py migrate --noinput && python manage.py create_admin
# worker/clock: optional dedicated processes, only useful on platforms/plans
# with free or affordable extra dynos. By default (RUN_INLINE_NOTIFICATION_WORKER=True
# on the web env), the web process already delivers notifications and checks
# the weekly reminder itself — see apps/notifications/background.py.
worker: cd fincorya && python manage.py notification_worker
clock: cd fincorya && python manage.py run_notification_scheduler
