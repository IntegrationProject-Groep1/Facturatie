import os
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv

# Laad de variabelen uit de .env file
load_dotenv()

# --- Configuratie uit ENV ---
BASE_URL = os.getenv("FOSSBILLING_URL").rstrip('/')
API_KEY = os.getenv("FOSSBILLING_API_KEY")
API_USER = "admin"  # Of haal dit ook uit je .env als dat varieert

# --- Functies ---

def extract_invoice_id_from_xml(xml_string):
    """Haalt het factuur-ID uit een XML string."""
    try:
        root = ET.fromstring(xml_string)
        # Zoek naar de tag 'invoice_id'
        invoice_id = root.find('invoice_id').text
        return invoice_id
    except (ET.ParseError, AttributeError) as e:
        print(f"Fout bij het parsen van XML: {e}")
        return None

def get_invoice_details(invoice_id):
    """Haalt de huidige data van een factuur op."""
    # We plakken het pad aan de base URL
    endpoint = f"{BASE_URL}/api/admin/invoice/get"
    
    response = requests.post(
        endpoint, 
        auth=(API_USER, API_KEY), 
        json={"id": invoice_id}
    )
    
    if response.status_code == 200:
        return response.json().get('result')
    return None

def mark_invoice_as_paid(invoice_id):
    """Zet de status van de factuur op betaald."""
    endpoint = f"{BASE_URL}/api/admin/invoice/mark_as_paid"
    
    response = requests.post(
        endpoint, 
        auth=(API_USER, API_KEY), 
        json={"id": invoice_id}
    )
    
    if response.status_code == 200:
        print(f"Succes: Factuur {invoice_id} is nu betaald.")
        return True
    else:
        print(f"Update mislukt voor {invoice_id}: {response.text}")
        return False

# --- Voorbeeld van uitvoering ---

xml_input = "<data><invoice_id>55</invoice_id></data>"
id_from_xml = extract_invoice_id_from_xml(xml_input)

if id_from_xml:
    # 1. Haal details op
    invoice = get_invoice_details(id_from_xml)
    
    if invoice:
        # 2. Update status als dat nodig is
        if invoice.get('status') != 'paid':
            mark_invoice_as_paid(id_from_xml)
        else:
            print(f"Factuur {id_from_xml} was al betaald.")