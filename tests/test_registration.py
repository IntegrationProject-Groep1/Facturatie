import uuid
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from src.services.rabbitmq_sender import send_message

def send_b2b_registration():
    # Unieke ID's voor deze test-run
    msg_id = str(uuid.uuid4())
    unique_suffix = uuid.uuid4().hex[:6]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    root = ET.Element("message")

    # Header
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "message_id").text = msg_id
    ET.SubElement(header, "version").text = "2.0"
    ET.SubElement(header, "type").text = "new_registration"
    ET.SubElement(header, "timestamp").text = timestamp
    ET.SubElement(header, "source").text = "crm_system"

    # Body
    body = ET.SubElement(root, "body")
    customer = ET.SubElement(body, "customer")
    
    # Klantgegevens
    ET.SubElement(customer, "id").text = f"CRM-{unique_suffix}"
    ET.SubElement(customer, "email").text = f"contact_{unique_suffix}@bedrijf.be"
    ET.SubElement(customer, "first_name").text = "Jan"
    ET.SubElement(customer, "last_name").text = "Janssen"
    
    # B2B velden
    ET.SubElement(customer, "is_company_linked").text = "true"
    ET.SubElement(customer, "company_name").text = "Acme Corp"
    ET.SubElement(customer, "company_id").text = "COMP-999" # Jouw nieuwe ID

    # Adres
    address = ET.SubElement(customer, "address")
    ET.SubElement(address, "street").text = "Bedrijfslaan"
    ET.SubElement(address, "number").text = "1"
    ET.SubElement(address, "postal_code").text = "2000"
    ET.SubElement(address, "city").text = "Antwerpen"
    ET.SubElement(address, "country").text = "Belgium"

    # Fee
    fee = ET.SubElement(body, "registration_fee", currency="EUR")
    fee.text = "0.00"

    # Genereer XML
    ET.indent(root, space="    ")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")

    print(f"[TEST] Sending B2B registration for company_id: COMP-999")
    send_message(xml_str, routing_key="crm.to.facturatie")

if __name__ == "__main__":
    send_b2b_registration()