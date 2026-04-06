from src.utils.xml_validator import validate_xml

xml = """<?xml version="1.0" encoding="UTF-8"?>
<message>
  <header>
    <message_id>550e8400-e29b-41d4-a716-446655440000</message_id>
    <version>2.0</version>
    <type>new_registration</type>
    <timestamp>2026-04-06T14:30:00Z</timestamp>
    <source>crm_system</source>
  </header>
  <body>
    <customer>
      <id>CUST-9999</id>
      <email>jan.janssens@example.com</email>
      <is_company_linked>false</is_company_linked>
      <company_name></company_name>
      <address>
        <street>Kerkstraat</street>
        <number>10</number>
        <postal_code>1000</postal_code>
        <city>Brussel</city>
        <country>België</country>
      </address>
    </customer>
    <registration_fee currency="eur">25.00</registration_fee>
  </body>
</message>
"""

ok, err = validate_xml(xml, "new_registration")
print("VALID:", ok)
print("ERROR:", err)