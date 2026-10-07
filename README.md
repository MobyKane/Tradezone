# TradeZone

TradeZone is a multi-vendor e-commerce platform designed to connect vendors and customers in a seamless and user-friendly environment. The platform allows vendors to list their products, manage their stores, and process orders, while customers can browse, search, and purchase products with ease. TradeZone is built using Django, styled with Tailwind CSS, and integrates Paystack for secure payment processing.

## Environment variables

Set these names in Render's environment settings. A blank local `.env` example is
provided in `.env.example`; do not commit `.env` or database files.

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL`
- `DJANGO_SUPERUSER_USERNAME`
- `DJANGO_SUPERUSER_EMAIL`
- `DJANGO_SUPERUSER_PASSWORD`
- `PAYSTACK_PUBLIC_KEY`
- `PAYSTACK_SECRET_KEY`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_USE_TLS`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `DEFAULT_FROM_EMAIL`
- `COMPLAINTS_EMAIL`
- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`

`DATABASE_URL` takes precedence when set. Without it, Django uses the local
SQLite database. Cloudinary is enabled only when all three Cloudinary credentials
are set; otherwise uploaded media uses local storage. Mail uses Django's console
backend when `EMAIL_HOST` is empty.

## Render deployment

Create a Render **Web Service** with:

- Build command: `bash build.sh`
- Start command: `gunicorn tradezone_core.wsgi:application --bind 0.0.0.0:$PORT`

The WSGI application is `tradezone_core.wsgi:application`. The Paystack webhook
path is `/webhooks/paystack/`; configure Paystack with the full public URL for
your deployed host.

Set `DEBUG` to `False`, set `ALLOWED_HOSTS` to the service hostname, and set
`CSRF_TRUSTED_ORIGINS` to the corresponding HTTPS origin. Attach a persistent
Render PostgreSQL database and set `DATABASE_URL` to its connection URL. Set
the `DJANGO_SUPERUSER_*` values to provision the initial admin account; build
skips creation when that username already exists. Set all three Cloudinary
values to persist uploaded images outside Render's temporary filesystem.

The build script installs requirements, collects static files, applies database
migrations, and runs the repeatable `seed_deployment_data` command. This creates
missing Fashion (Men, Women, Unisex) and Building Materials (Spanish Tiles,
Doors, Other Building Materials) category nodes, 10% commission defaults for
each product category, and the platform payment settings defaults. Existing
category/commission/settings values are preserved.

---

## Project Overview

TradeZone is a scalable and modular e-commerce solution that supports multiple vendors. Key features include:
- Vendor management: Vendors can create and manage their stores and product listings.
- Product catalog: Customers can browse and search for products across multiple categories.
- Secure payments: Integrated with Paystack for secure and reliable payment processing.
- Responsive design: Built with Tailwind CSS for a modern and mobile-friendly user interface.

---

## Tech Stack

- **Backend**: Django (Python)
- **Frontend**: Tailwind CSS (via Stitch templates)
- **Payment Integration**: Paystack
- **Database**: SQLite (default, can be replaced with PostgreSQL or MySQL for production)
- **Other Tools**: Django ORM, Django Template Inheritance

---

## Project Structure

The project is organized into the following key apps:

- **`vendors`**: Handles vendor-related functionality, including store creation, management, and vendor profiles.
- **`products`**: Manages product listings, categories, and product details.

### Directory Structure