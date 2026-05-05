from src.utils.xml_validator import validate_xml


NEW_REGISTRATION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>550e8400-e29b-41d4-a716-446655440000</message_id>
    <master_uuid>01890a5d-ac96-7ab2-80e2-4536629c90de</master_uuid>
    <version>2.0</version>
    <type>new_registration</type>
    <timestamp>2026-04-06T14:30:00Z</timestamp>
    <source>crm_system</source>
  </header>
  <body>
    <customer>
      <customer_id>CUST-9999</customer_id>
      <email>jan.janssens@example.com</email>
      <first_name>Jan</first_name>
      <last_name>Janssens</last_name>
      <is_company_linked>false</is_company_linked>
      <address>
        <street>Kerkstraat</street>
        <number>10</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>Belgie</country>
      </address>
    </customer>
    <registration_fee currency="eur">25.00</registration_fee>
  </body>
</message>
"""


def test_valid_new_registration_xsd():
    ok, err = validate_xml(NEW_REGISTRATION_XML, "new_registration")
    assert ok is True, f"XSD validation failed: {err}"
