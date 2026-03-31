import pytest
from unittest.mock import patch, MagicMock
from src.services.fossbilling_api import (
    _create_client,
    _create_invoice,
    create_registration_invoice,
    MAX_RETRIES,
)

CUSTOMER_DATA = {
    "email": "info@bedrijf.be",
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


# _create_invoice tests

def test_create_invoice_returns_invoice_id() -> None:
    """_create_invoice must return the invoice_id from the API response."""
    with patch("src.services.fossbilling_api.requests.post", return_value=mock_post_response("INV-2026-001")):
        invoice_id = _create_invoice(42, "150.00", "eur")
    assert invoice_id == "INV-2026-001"


def test_create_invoice_raises_on_api_error() -> None:
    """_create_invoice must raise an Exception when the API returns no result."""
    mock = MagicMock()
    mock.json.return_value = {"error": {"message": "client not found"}}
    mock.raise_for_status = MagicMock()
    with patch("src.services.fossbilling_api.requests.post", return_value=mock):
        with pytest.raises(Exception, match="FossBilling API error"):
            _create_invoice(42, "150.00", "eur")


# create_registration_invoice tests

def test_create_registration_invoice_success() -> None:
    """create_registration_invoice must return an invoice_id on first successful attempt."""
    responses = [mock_post_response(42), mock_post_response("INV-2026-001")]
    with patch("src.services.fossbilling_api.requests.post", side_effect=responses):
        invoice_id = create_registration_invoice(CUSTOMER_DATA)
    assert invoice_id == "INV-2026-001"


def test_create_registration_invoice_retries_on_failure() -> None:
    """create_registration_invoice must retry and succeed on a later attempt."""
    fail = MagicMock()
    fail.json.return_value = {"error": {"message": "server error"}}
    fail.raise_for_status = MagicMock()
    success_client = mock_post_response(42)
    success_invoice = mock_post_response("INV-2026-002")

    # First attempt: client call fails. Second attempt: both succeed.
    with patch("src.services.fossbilling_api.requests.post", side_effect=[fail, success_client, success_invoice]):
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
