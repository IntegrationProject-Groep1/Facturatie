import pytest
from unittest.mock import patch, MagicMock
from src.services.fossbilling_api import (
    _create_client,
    _create_invoice,
    _get_client_by_email,
    _get_or_create_client,
    create_registration_invoice,
    MAX_RETRIES,
)

CUSTOMER_DATA = {
    "email": "info@bedrijf.be",
    "first_name": "Jan",
    "last_name": "Peeters",
    "company_name": "",
    "address": {
        "street": "Kiekenmarkt",
        "number": "42",
        "postal_code": "1000",
        "city": "Brussel",
        "country": "be",
    },
    "registration_fee": "150.00",
    "fee_currency": "eur",
}

CUSTOMER_DATA_COMPANY = {
    **CUSTOMER_DATA,
    "company_name": "Bedrijf NV",
}


def mock_post_response(result_value: any) -> MagicMock:
    """Returns a mock requests.post response with a given result value."""
    mock = MagicMock()
    mock.json.return_value = {"result": result_value}
    mock.raise_for_status = MagicMock()
    return mock


def mock_get_list_empty() -> MagicMock:
    """Returns a mock get_list response with no clients found."""
    mock = MagicMock()
    mock.json.return_value = {"result": {"list": [], "total": 0}}
    mock.raise_for_status = MagicMock()
    return mock


# _create_client tests

def test_create_client_returns_client_id() -> None:
    """_create_client must return the client_id from the API response."""
    with patch("src.services.fossbilling_api.requests.post", return_value=mock_post_response(42)):
        client_id = _create_client(CUSTOMER_DATA)
    assert client_id == 42


def test_create_client_includes_company_when_linked() -> None:
    """_create_client must include company in payload when company_name is set."""
    with patch("src.services.fossbilling_api.requests.post", return_value=mock_post_response(1)) as mock_post:
        _create_client(CUSTOMER_DATA_COMPANY)
    payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args.args[1] if mock_post.call_args.args else mock_post.call_args[1]["data"]
    assert payload.get("company") == "Bedrijf NV"


def test_create_client_raises_on_api_error() -> None:
    """_create_client must raise an Exception when the API returns no result."""
    mock = MagicMock()
    mock.json.return_value = {"error": {"message": "invalid email"}}
    mock.raise_for_status = MagicMock()
    with patch("src.services.fossbilling_api.requests.post", return_value=mock):
        with pytest.raises(Exception, match="FossBilling API error"):
            _create_client(CUSTOMER_DATA)


# _get_client_by_email tests

def test_get_client_by_email_returns_id_when_found() -> None:
    """_get_client_by_email must return the client_id when a match is found."""
    mock_result = {"result": {"list": [{"id": 7}], "total": 1}}
    mock = MagicMock()
    mock.json.return_value = mock_result
    mock.raise_for_status = MagicMock()
    with patch("src.services.fossbilling_api.requests.post", return_value=mock):
        client_id = _get_client_by_email("info@bedrijf.be")
    assert client_id == 7


def test_get_client_by_email_returns_none_when_not_found() -> None:
    """_get_client_by_email must return None when no client matches."""
    mock_result = {"result": {"list": [], "total": 0}}
    mock = MagicMock()
    mock.json.return_value = mock_result
    mock.raise_for_status = MagicMock()
    with patch("src.services.fossbilling_api.requests.post", return_value=mock):
        client_id = _get_client_by_email("nieuw@bedrijf.be")
    assert client_id is None


# _get_or_create_client tests

def test_get_or_create_client_returns_existing_id() -> None:
    """_get_or_create_client must return the existing client_id without creating a new one."""
    with patch("src.services.fossbilling_api._get_client_by_email", return_value=99):
        with patch("src.services.fossbilling_api._create_client") as mock_create:
            client_id = _get_or_create_client(CUSTOMER_DATA)
    assert client_id == 99
    mock_create.assert_not_called()


def test_get_or_create_client_creates_when_not_found() -> None:
    """_get_or_create_client must call _create_client when no existing client is found."""
    with patch("src.services.fossbilling_api._get_client_by_email", return_value=None):
        with patch("src.services.fossbilling_api._create_client", return_value=42) as mock_create:
            client_id = _get_or_create_client(CUSTOMER_DATA)
    assert client_id == 42
    mock_create.assert_called_once()


INVOICE_ITEMS = [{"title": "Inschrijvingskosten", "price": "150.00", "quantity": 1, "currency": "eur"}]
INVOICE_ITEMS_VAT = [{"title": "Ticket", "price": "50.00", "quantity": 2, "currency": "eur", "vat_rate": 21, "sku": "TICKET-001"}]


# _create_invoice tests

def test_create_invoice_returns_invoice_id() -> None:
    """_create_invoice must return the invoice_id from the API response."""
    with patch("src.services.fossbilling_api.requests.post", return_value=mock_post_response("INV-2026-001")):
        invoice_id = _create_invoice(42, INVOICE_ITEMS)
    assert invoice_id == "INV-2026-001"


def test_create_invoice_raises_on_api_error() -> None:
    """_create_invoice must raise an Exception when the API returns no result."""
    mock = MagicMock()
    mock.json.return_value = {"error": {"message": "client not found"}}
    mock.raise_for_status = MagicMock()
    with patch("src.services.fossbilling_api.requests.post", return_value=mock):
        with pytest.raises(Exception, match="FossBilling API error"):
            _create_invoice(42, INVOICE_ITEMS)


def test_create_invoice_sends_vat_rate_and_sku() -> None:
    """_create_invoice must include vat_rate and sku in the payload when provided."""
    with patch("src.services.fossbilling_api.requests.post", return_value=mock_post_response("INV-2026-002")) as mock_post:
        _create_invoice(42, INVOICE_ITEMS_VAT)
    payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
    assert payload.get("items[0][taxrate]") == 21
    assert payload.get("items[0][sku]") == "TICKET-001"


def test_create_invoice_supports_multiple_items() -> None:
    """_create_invoice must build payload entries for each item in the list."""
    items = [
        {"title": "Item A", "price": "10.00", "quantity": 1},
        {"title": "Item B", "price": "20.00", "quantity": 2},
    ]
    with patch("src.services.fossbilling_api.requests.post", return_value=mock_post_response("INV-2026-003")) as mock_post:
        _create_invoice(42, items)
    payload = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
    assert payload.get("items[0][title]") == "Item A"
    assert payload.get("items[1][title]") == "Item B"
    assert payload.get("items[1][quantity]") == 2


# create_registration_invoice tests

def test_create_registration_invoice_success() -> None:
    """create_registration_invoice must return an invoice_id on first successful attempt."""
    # _get_client_by_email (get_list) → no client found, _create_client → 42, _create_invoice → INV-2026-001
    responses = [mock_get_list_empty(), mock_post_response(42), mock_post_response("INV-2026-001")]
    with patch("src.services.fossbilling_api.requests.post", side_effect=responses):
        invoice_id = create_registration_invoice(CUSTOMER_DATA)
    assert invoice_id == "INV-2026-001"


def test_create_registration_invoice_retries_on_failure() -> None:
    """create_registration_invoice must retry and succeed on a later attempt."""
    fail = MagicMock()
    fail.json.return_value = {"error": {"message": "server error"}}
    fail.raise_for_status = MagicMock()

    # Attempt 1: _get_client_by_email fails → retry
    # Attempt 2: _get_client_by_email → no client, _create_client → 42, _create_invoice → INV-2026-002
    responses = [fail, mock_get_list_empty(), mock_post_response(42), mock_post_response("INV-2026-002")]
    with patch("src.services.fossbilling_api.requests.post", side_effect=responses):
        with patch("src.services.fossbilling_api.time.sleep"):
            invoice_id = create_registration_invoice(CUSTOMER_DATA)
    assert invoice_id == "INV-2026-002"


def test_create_registration_invoice_raises_after_max_retries() -> None:
    """create_registration_invoice must raise an Exception after MAX_RETRIES failures."""
    fail = MagicMock()
    fail.json.return_value = {"error": {"message": "server error"}}
    fail.raise_for_status = MagicMock()

    with patch("src.services.fossbilling_api.requests.post", side_effect=[fail] * (MAX_RETRIES * 2)):
        with patch("src.services.fossbilling_api.time.sleep"):
            with pytest.raises(Exception, match=f"after {MAX_RETRIES} attempts"):
                create_registration_invoice(CUSTOMER_DATA)


def test_create_registration_invoice_exact_retry_count() -> None:
    """create_registration_invoice must attempt exactly MAX_RETRIES times before giving up."""
    fail = MagicMock()
    fail.json.return_value = {"error": {"message": "server error"}}
    fail.raise_for_status = MagicMock()

    with patch("src.services.fossbilling_api.requests.post", side_effect=[fail] * (MAX_RETRIES * 2)) as mock_post:
        with patch("src.services.fossbilling_api.time.sleep"):
            with pytest.raises(Exception):
                create_registration_invoice(CUSTOMER_DATA)
    assert mock_post.call_count == MAX_RETRIES
