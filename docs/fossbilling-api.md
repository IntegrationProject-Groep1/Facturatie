# FossBilling API Client

**File:** `src/services/fossbilling_api.py`

---

## Authentication

All API calls use HTTP Basic Auth via `_api_post()`. Set the following environment variables:

| Variable | Description | Example |
|---|---|---|
| `BILLING_API_URL` | Base URL of the FossBilling admin API | `https://facturatie.desiderius.me/api` |
| `BILLING_API_USERNAME` | Admin username | `admin` |
| `BILLING_API_TOKEN` | API token (generate in FossBilling admin → Account → API tokens) | `D8tW...` |

Every request also sends `X-Forwarded-Proto: https` to prevent redirect loops when FossBilling is configured with an `https://` base URL behind a reverse proxy.

---

## Constants

| Constant | Value | Description |
|---|---|---|
| `MAX_RETRIES` | `3` | Maximum attempts before raising an exception |
| `RETRY_DELAY_SECONDS` | `2` | Seconds to wait between retry attempts |

---

## Exceptions

| Exception | When raised |
|---|---|
| `FossBillingNotFoundError` | FossBilling confirms the resource does not exist (not a transient error — do not retry) |

---

## Public functions

### `create_registration_invoice(customer_data)`

Full flow for a new registration: gets or creates a FossBilling client, then creates an invoice with a single `Inschrijvingskosten` line item. Retries up to `MAX_RETRIES` times on failure.

If `customer_data["payment_status"] == "paid"`, the invoice is immediately marked as paid via `mark_invoice_as_paid()`.

| Parameter | Type | Description |
|---|---|---|
| `customer_data` | `dict` | See `_create_client` fields below, plus `registration_fee` and `fee_currency` |

**Returns:** `str` (invoice_id)  
**Raises:** `Exception` after all retries exhausted

---

### `get_invoice(invoice_id)`

Fetches the full invoice object from FossBilling.

| Parameter | Type | Description |
|---|---|---|
| `invoice_id` | `str` | FossBilling invoice ID |

**Returns:** `dict` (invoice object) or `None` if not found  
**Raises:** `Exception` for transient errors (network, API unreachable) — do not swallow these

**FossBilling endpoint:** `POST admin/invoice/get`

---

### `get_invoice_status(invoice_id)`

Returns the status string of an invoice.

| Return value | Meaning |
|---|---|
| `"paid"` | Invoice has been paid |
| `"unpaid"` | Invoice is open |
| `"cancelled"` | Invoice was cancelled |
| `None` | Invoice not found |

**Raises:** `Exception` for transient errors

---

### `get_invoice_type(invoice)`

Determines whether an invoice is a registration or consumption invoice by inspecting its line items. Accepts an already-fetched invoice dict (not an ID) to avoid a redundant API call.

| Return value | Meaning |
|---|---|
| `"registration"` | Any line title contains `"inschrijvingskosten"` (case-insensitive) |
| `"consumption"` | All other invoices |

---

### `pay_invoice(invoice_id, amount)`

Marks an invoice as paid by setting `status=paid` and `paid_at` (current UTC timestamp in `YYYY-MM-DD HH:MM:SS` format).

| Parameter | Type | Description |
|---|---|---|
| `invoice_id` | `str` | FossBilling invoice ID |
| `amount` | `str` | Amount paid (e.g. `"150.00"`) — passed for logging only, not sent to API |

**Returns:** `True` on success, `False` on failure (also sends a log message to the `logs` queue on failure)

**FossBilling endpoint:** `POST admin/invoice/update`

---

### `mark_invoice_as_paid(invoice_id)`

Marks an invoice as paid using the Custom payment gateway. Used when a registration arrives with `payment_status=paid`. First looks up the Custom gateway ID, then sets it on the invoice and calls `mark_as_paid`.

**FossBilling endpoints:** `POST admin/invoice/gateway_get_list`, `POST admin/invoice/update`, `POST admin/invoice/mark_as_paid`

---

### `cancel_invoice(invoice_id)`

Sets invoice status to `cancelled`.

**Returns:** `True` on success, `False` on failure

**FossBilling endpoint:** `POST admin/invoice/update`

---

### `create_credit_note(invoice)`

Creates a negative invoice (credit note) for a paid registration invoice. Accepts an already-fetched invoice dict. Creates mirrored line items with negated prices and titles prefixed with `"Creditnota: "`.

**Returns:** `str` (credit note invoice_id)  
**Raises:** `Exception` if the invoice has no line items, or if creation fails

---

### `process_consumption_order(company_id, items, company_name, first_name, last_name, email)`

Creates one consolidated invoice for a company with all provided items. Gets or creates the company billing account, then calls `_create_invoice`. Retries up to `MAX_RETRIES` times. If a `FossBillingNotFoundError` occurs for a client, the cached client ID is cleared and the call is retried.

| Parameter | Type | Description |
|---|---|---|
| `company_id` | `str` | Internal company identifier — used as the billing account key |
| `items` | `list[dict]` | List of item dicts with `description`, `price`, `quantity`, `vat_rate` |
| `company_name` | `str` | Display name on the invoice |
| `first_name` | `str` | Optional — used when creating a new billing account |
| `last_name` | `str` | Optional — used when creating a new billing account |
| `email` | `str \| None` | Customer email — falls back to generated `billing.<company_id>@facturatie.be` |

**Returns:** `str` (invoice_id)  
**Raises:** `Exception` after all retries exhausted

---

### `update_client(client_id, customer_data)`

Updates an existing FossBilling client with new contact and address data.

| Parameter | Type | Description |
|---|---|---|
| `client_id` | `int` | FossBilling client ID |
| `customer_data` | `dict` | Same structure as `_create_client` |

**Raises:** `Exception` on failure

**FossBilling endpoint:** `POST admin/client/update`

---

### `update_client_by_identity_uuid(identity_uuid, email, first_name, last_name, company_name, vat_number)`

Looks up a client by email, then calls `update_client`. Used by the `profile_update` flow.

**Returns:** `True` on success, `False` if client not found or update fails

---

### `get_client_by_company_id(company_id)`

Searches for a FossBilling client by `company_id` (matches on `company` field or client `id`).

**Returns:** `int` (client_id) or `None`

**FossBilling endpoint:** `POST admin/client/get_list`

---

### `get_unpaid_invoice_for_client(client_id)`

Returns the first unpaid invoice for a given client.

**Returns:** `str` (invoice_id) or `None`

**FossBilling endpoint:** `POST admin/invoice/get_list`

---

### `add_item_to_invoice(invoice_id, item)`

Adds a single line item to an existing open invoice.

**Item dict fields:** `title` (required), `price` (required), `quantity` (optional), `vat_rate` (optional)

**FossBilling endpoint:** `POST admin/invoice/item_add`

---

## Internal functions

These are not called directly by the receiver — they are implementation details used by the public functions above.

| Function | Description |
|---|---|
| `_api_post(endpoint, data)` | Authenticated POST to FossBilling. Raises `FossBillingNotFoundError` on "not found" responses, `Exception` on other API errors |
| `_create_client(customer_data)` | Creates a new FossBilling client. Auto-generates a random password. Returns `int` (client_id) |
| `_get_client_by_email(email)` | Looks up a client by email. Returns `int` or `None` |
| `_get_or_create_client(customer_data)` | Returns existing client_id if email is known, else creates a new one. Ensures idempotency |
| `_billing_email(company_id)` | Generates a deterministic email for a company billing account: `billing.<company_id_slugified>@facturatie.be` |
| `_get_or_create_billing_client(...)` | Gets the billing account for a company from MySQL cache → FossBilling lookup → create. Saves the result to MySQL |
| `_get_custom_gateway_id()` | Fetches the database ID of the `Custom` payment gateway from FossBilling |

---

## `_create_client` — customer_data fields

| Field | Required | Description |
|---|---|---|
| `email` | Yes | Email address |
| `first_name` | No | First name (default: `"Unknown"`) |
| `last_name` | No | Last name (default: `"-"`) |
| `company_name` | No | Included as `company` if provided |
| `fee_currency` | No | Currency for the invoice (default: `"eur"`) |
| `address.street` | No | Street name |
| `address.number` | No | House number |
| `address.postal_code` | No | Postal code |
| `address.city` | No | City |
| `address.country` | No | Country code (converted to uppercase) |

---

## Function summary

| Function | Endpoint | Returns |
|---|---|---|
| `create_registration_invoice` | `client/create`, `invoice/prepare` | `str` (invoice_id) |
| `get_invoice` | `invoice/get` | `dict \| None` |
| `get_invoice_status` | via `get_invoice` | `str \| None` |
| `get_invoice_type` | — (inspects dict) | `"registration" \| "consumption"` |
| `pay_invoice` | `invoice/update` | `bool` |
| `mark_invoice_as_paid` | `invoice/update`, `invoice/mark_as_paid` | `None` |
| `cancel_invoice` | `invoice/update` | `bool` |
| `create_credit_note` | `invoice/prepare` | `str` (credit note id) |
| `process_consumption_order` | `client/create`, `invoice/prepare` | `str` (invoice_id) |
| `update_client` | `client/update` | `None` |
| `update_client_by_identity_uuid` | `client/get_list`, `client/update` | `bool` |
| `get_client_by_company_id` | `client/get_list` | `int \| None` |
| `get_unpaid_invoice_for_client` | `invoice/get_list` | `str \| None` |
| `add_item_to_invoice` | `invoice/item_add` | `None` |
