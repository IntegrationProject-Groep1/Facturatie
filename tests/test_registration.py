import uuid
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from src.services.rabbitmq_sender import send_message

def send_valid_registration():
    # Unieke ID's voor deze test-run
    msg_id = str(uuid.uuid4())
    unique_suffix = uuid.uuid4().hex[:6]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Gebruik een 'plat' ElementTree object zonder handmatige strings of indents
    root = ET.Element("message")

    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = "crm_system"

    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    ET.SubElement(customer, "id").text = f"CRM-{unique_suffix}"

    # CRUCIAAL: Gebruik een simpel format zonder spaties
    ET.SubElement(customer, "email").text = f"user_{unique_suffix}@facturatie.be"

    ET.SubElement(customer, "is_company_linked").text = "false"

    # Adres (volgorde is belangrijk voor XSD)
    address = ET.SubElement(customer, "address")
    ET.SubElement(address, "street").text = "Kerkstraat"
    ET.SubElement(address, "number").text = "10"
    ET.SubElement(address, "postal_code").text = "1000"
    ET.SubElement(address, "city").text = "Brussel"
    ET.SubElement(address, "country").text = "Belgium"

    # Fee
    fee = ET.SubElement(body, "registration_fee", currency="USD")
    fee.text = "15.00"

    # Genereer XML zonder ET.indent() of handmatige \n na de header
    ET.indent(root, space="    ")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")

    print(f"[TEST] Sending message with email: user_{unique_suffix}@facturatie.be")
    send_message(xml_str, routing_key="crm.to.facturatie")

if __name__ == "__main__":
    send_valid_registration()
