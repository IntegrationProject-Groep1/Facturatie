from lxml import etree
import os

# Cache for the compiled schemas
_cached_schemas = {}


def get_xsd_validator(name: str):
    """Retrieves the validator from the cache or loads it from disk."""
    if name not in _cached_schemas:
        path = os.path.join(os.getcwd(), "src", "services", "xsd", f"{name}.xsd")

        if not os.path.exists(path):
            raise FileNotFoundError(f"XSD bestand niet gevonden: {path}")

        with open(path, "rb") as f:
            schema_doc = etree.XML(f.read())
            _cached_schemas[name] = etree.XMLSchema(schema_doc)

    return _cached_schemas[name]


def validate_xml(xml_string: str, schema_name: str):
    """Validates an XML string against a specific schema."""
    try:
        validator = get_xsd_validator(schema_name)
        xml_doc = etree.fromstring(xml_string.encode("utf-8"))
        validator.assertValid(xml_doc)
        return True, None

    except etree.DocumentInvalid as e:
        # Returns the specific validation error
        return False, str(e)

    except Exception as e:
        return False, f"Systeemfout bij validatie: {str(e)}"