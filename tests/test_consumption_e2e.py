"""
Local end-to-end integration test — requires MySQL on localhost, no FossBilling needed.

Run:
    $env:MYSQL_HOST = "localhost"; pytest -m integration -v
"""

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mysql.connector
import pytest

from src.services.consumption_store import init_db, save_items
from src.services.rabbitmq_receiver import process_message

COMPANY_ID = "FOSS-CUST-102"
COMPANY_NAME = "Bedrijf NV"
EMAIL = "info@bedrijf.be"
FAKE_INVOICE_ID = "INV-LOCAL-TEST-001"


def _reset_table():
    conn = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        database=os.getenv("MYSQL_DATABASE", "fossbilling"),
        user=os.getenv("MYSQL_USER", "fossbilling"),
        password=os.getenv("MYSQL_PASSWORD", "fossbilling"),
    )
    conn.cursor().execute("DROP TABLE IF EXISTS pending_consumptions")
    conn.commit()
    conn.close()
    init_db()


def _build_event_ended_xml() -> bytes:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>{uuid.uuid4()}</message_id>
    <version>2.0</version>
    <type>event_ended</type>
    <timestamp>{ts}</timestamp>
    <source>frontend</source>
  </header>
  <body>
    <session_id>SESSION-TEST-001</session_id>
    <ended_at>{ts}</ended_at>
  </body>
</message>""".encode("utf-8")


@pytest.mark.integration
def test_consumption_flow_e2e():
    """
    Full flow: save items for two badge holders, trigger event_ended,
    verify one consolidated invoice is sent to mailing.
    Requires MySQL on localhost. FossBilling is mocked.
    """
    _reset_table()

    save_items(
        company_id=COMPANY_ID,
        badge_id="BADGE-001",
        master_uuid="BADGE-001",
        items=[
            {"description": "Coca-Cola", "price": "2.50", "quantity": 1, "vat_rate": "21"},
            {"description": "Water", "price": "1.50", "quantity": 2, "vat_rate": "6"},
        ],
        email=EMAIL,
        company_name=COMPANY_NAME,
    )

    save_items(
        company_id=COMPANY_ID,
        badge_id="BADGE-002",
        master_uuid="BADGE-002",
        items=[
            {"description": "Fanta", "price": "2.50", "quantity": 3, "vat_rate": "21"},
        ],
        email=EMAIL,
        company_name=COMPANY_NAME,
    )

    channel = MagicMock()
    method = MagicMock()
    method.delivery_tag = 1
    sent_to_mailing = []

    def capture_send_message(xml, routing_key=None, **_):
        sent_to_mailing.append({"routing_key": routing_key, "xml": xml})

    with patch(
        "src.services.rabbitmq_receiver.fossbilling_client.process_consumption_order",
        return_value=FAKE_INVOICE_ID,
    ), patch(
        "src.services.rabbitmq_receiver.send_message",
        side_effect=capture_send_message,
    ), patch(
        "src.services.rabbitmq_receiver.validate_xml",
        return_value=(True, None),
    ), patch(
        "src.services.rabbitmq_receiver.is_duplicate",
        return_value=False,
    ):
        process_message(channel, method, MagicMock(), _build_event_ended_xml())

    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()
    assert len(sent_to_mailing) == 1
    assert sent_to_mailing[0]["routing_key"] == "facturatie.to.mailing"
