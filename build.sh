#!/usr/bin/env bash
set -eu

python -m pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_deployment_data

if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] \
    && [ -n "${DJANGO_SUPERUSER_EMAIL:-}" ] \
    && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    user_exists="$(
        python manage.py shell --verbosity 0 -c \
            "from django.contrib.auth import get_user_model; import os; print('yes' if get_user_model().objects.filter(username=os.environ['DJANGO_SUPERUSER_USERNAME']).exists() else 'no')"
    )"
    case "$user_exists" in
        yes)
            echo "Deployment superuser already exists; skipping creation."
            ;;
        no)
            python manage.py createsuperuser --noinput
            ;;
        *)
            echo "Could not determine whether the deployment superuser exists." >&2
            exit 1
            ;;
    esac
else
    echo "Skipping deployment superuser creation; set all DJANGO_SUPERUSER_* variables."
fi
