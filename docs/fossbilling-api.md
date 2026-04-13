# FossBilling API Client

**File:** `src/services/fossbilling_api.py`
**Date:** 2026-04-05

---

## Configuration

Authentication via HTTP Basic Auth. Set the following environment variables:

| Variable | Description | Example |
|---|---|---|
| `BILLING_API_URL` | Base URL of the FossBilling API | `https://server:30010/api` |
| `BILLING_API_USERNAME` | Admin username | `admin` |
| `BILLING_API_TOKEN` | API token (generate via FossBilling admin panel) | `D8tW...` |

> **Note:** SSL certificate verification is enabled. The API URL must use a valid certificate (e.g. via the Cloudflare tunnel at `https://facturatie.desiderius.me/api`).

---

## Constants

| Constant | Value | Description |
|---|---|---|
| `MAX_RETRIES` | `3` | Maximum number of attempts on failure |
| `RETRY_DELAY_SECONDS` | `2` | Wait time in seconds between attempts |

---

## Functions

### `_get_client_by_email(email)`

Looks up an existing client by email address.

| Parameter | Type | Description |
|---|---|---|
| `email` | `str` | Email address of the client |

**Returns:** `int` (client_id) if found, `None` if not found.

**FossBilling endpoint:** `POST admin/client/get_list`

---

### `_get_or_create_client(customer_data)`

Returns the existing `client_id` if the email is already registered. Otherwise creates a new client. Ensures **idempotency** on message redelivery.

| Parameter | Type | Description |
|---|---|---|
| `customer_data` | `dict` | See `_create_client` |

**Returns:** `int` (client_id)

---

### `_create_client(customer_data)`

Creates a new client in FossBilling.

**Expected fields in `customer_data`:**

| Field | Required | Description |
|---|---|---|
| `email` | Yes | Email address |
| `first_name` | No | First name (default: `"Unknown"`) |
| `last_name` | No | Last name (default: `"-"`) |
| `company_name` | No | Company name — included as `company` if provided |
| `fee_currency` | No | Currency (default: `"eur"`) |
| `address.street` | No | Street name |
| `address.number` | No | House number |
| `address.postal_code` | No | Postal code |
| `address.city` | No | City |
| `address.country` | No | Country (converted to uppercase) |

**Returns:** `int` (client_id)

**FossBilling endpoint:** `POST admin/client/create`

---

### `update_client(client_id, customer_data)`

Updates an existing client in FossBilling. Raises an `Exception` on failure.

| Parameter | Type | Description |
|---|---|---|
| `client_id` | `int` | ID of the client in FossBilling |
| `customer_data` | `dict` | Same structure as `_create_client` |

**FossBilling endpoint:** `POST admin/client/update`

---

### `_create_invoice(client_id, items)`

Creates an invoice for a client in FossBilling.

| Parameter | Type | Description |
|---|---|---|
| `client_id` | `int` | ID of the client |
| `items` | `list[dict]` | List of invoice line items |

**Item structure:**

| Field | Required | Description |
|---|---|---|
| `title` | Yes | Description of the item |
| `price` | Yes | Price as string (e.g. `"150.00"`) |
| `quantity` | No | Quantity (default: `1`) |
| `currency` | No | Currency (e.g. `"eur"`) |
| `vat_rate` | No | VAT percentage (e.g. `21`) |
| `sku` | No | Stock keeping unit / article code |

**Returns:** `str` (invoice_id)

**FossBilling endpoint:** `POST admin/invoice/prepare`

---

### `create_registration_invoice(customer_data)`

Full flow for creating a registration invoice. Internally calls `_get_or_create_client` and `_create_invoice` with retry logic.

| Parameter | Type | Description |
|---|---|---|
| `customer_data` | `dict` | Same as `_create_client`, plus `registration_fee` and `fee_currency` |

**Returns:** `str` (invoice_id) on success.

**Raises:** `Exception` after `MAX_RETRIES` failed attempts.

---

### `pay_invoice(invoice_id, amount)`

Marks an invoice as paid in FossBilling.

| Parameter | Type | Description |
|---|---|---|
| `invoice_id` | `str` | ID of the invoice |
| `amount` | `str` | Amount paid (e.g. `"150.00"`) |

**Returns:** `True` on success, `False` on failure.

**FossBilling endpoint:** `POST admin/invoice/pay`

---

### `cancel_invoice(invoice_id)`

Cancels an invoice in FossBilling by setting its status to `cancelled`. The invoice remains visible as a credit note.

| Parameter | Type | Description |
|---|---|---|
| `invoice_id` | `str` | ID of the invoice |

**Returns:** `True` on success, `False` on failure.

**FossBilling endpoint:** `POST admin/invoice/update`

---

## Summary

| Function | Endpoint | Returns |
|---|---|---|
| `_get_client_by_email` | `admin/client/get_list` | `int \| None` |
| `_get_or_create_client` | — | `int` |
| `_create_client` | `admin/client/create` | `int` |
| `update_client` | `admin/client/update` | `None` (or Exception) |
| `_create_invoice` | `admin/invoice/prepare` | `str` |
| `create_registration_invoice` | — | `str` (or Exception) |
| `pay_invoice` | `admin/invoice/pay` | `bool` |
| `cancel_invoice` | `admin/invoice/update` | `bool` |
