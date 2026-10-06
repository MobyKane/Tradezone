# TradeZone

TradeZone is a multi-vendor e-commerce platform designed to connect vendors and customers in a seamless and user-friendly environment. The platform allows vendors to list their products, manage their stores, and process orders, while customers can browse, search, and purchase products with ease. TradeZone is built using Django, styled with Tailwind CSS, and integrates Paystack for secure payment processing.

## Environment variables

Set the following values in a local `.env` file for development or in your deployment environment:

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `DB_ENGINE`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `PAYSTACK_PUBLIC_KEY`
- `PAYSTACK_SECRET_KEY`

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