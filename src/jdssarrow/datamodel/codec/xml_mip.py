"""Default XML codec — a MIP-3.1-variant representation of the JDSSDM (Vol II).

AEP-76's data model is a variant of MIP 3.1 XML messages. This codec renders a
:class:`JdssMessage` as a compact ``<JdssMessage>`` document with a header block and a
typed body block, and validates inbound documents against the bundled XSD when ``lxml`` is
available. The mapping is intentionally faithful to the *shape* of MIP object-item reporting
(originator, reporting time, location, SIDC) without pulling in the full JC3IEDM.

If ``lxml`` is not installed the codec falls back to the stdlib ``ElementTree`` and skips
schema validation, so the rest of the system keeps working in minimal environments.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from jdssarrow.datamodel.messages import JdssMessage, message_from_dict

try:  # pragma: no cover - exercised indirectly
    from lxml import etree as _lxml_etree

    _HAVE_LXML = True
except Exception:  # pragma: no cover
    import xml.etree.ElementTree as _std_etree

    _HAVE_LXML = False

NS = "urn:nato:aep76:jdssdm"


def _to_xml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _dict_to_elements(builder: Any, parent: Any, data: dict) -> None:
    """Recursively serialize a plain dict into child elements under ``parent``."""
    for key, value in data.items():
        if isinstance(value, dict):
            child = builder.SubElement(parent, key)
            _dict_to_elements(builder, child, value)
        elif isinstance(value, list):
            container = builder.SubElement(parent, key)
            for item in value:
                entry = builder.SubElement(container, "item")
                if isinstance(item, dict):
                    _dict_to_elements(builder, entry, item)
                else:
                    entry.text = _to_xml_value(item)
        elif value is None:
            builder.SubElement(parent, key)  # empty element = null
        else:
            child = builder.SubElement(parent, key)
            child.text = _to_xml_value(value)


def _elements_to_dict(element: Any) -> Any:
    """Inverse of :func:`_dict_to_elements`."""
    children = list(element)
    if not children:
        return element.text
    tag_of = lambda e: e.tag.split("}")[-1]  # noqa: E731 - strip namespace
    if all(tag_of(c) == "item" for c in children):
        return [_elements_to_dict(c) for c in children]
    return {tag_of(c): _elements_to_dict(c) for c in children}


class _LxmlBuilder:
    Element = staticmethod(lambda tag: _lxml_etree.Element(tag))
    SubElement = staticmethod(lambda parent, tag: _lxml_etree.SubElement(parent, tag))


class _StdBuilder:
    Element = staticmethod(lambda tag: _std_etree.Element(tag))
    SubElement = staticmethod(lambda parent, tag: _std_etree.SubElement(parent, tag))


class XmlMipCodec:
    """Serialize JDSSDM messages as MIP-3.1-variant XML with optional XSD validation."""

    name = "xml"
    content_type = "application/vnd.nato.jdssdm+xml"

    def __init__(self, validate: bool = True) -> None:
        self._builder = _LxmlBuilder if _HAVE_LXML else _StdBuilder
        self._schema = self._load_schema() if (validate and _HAVE_LXML) else None

    @staticmethod
    def _load_schema() -> Any | None:
        try:
            xsd = resources.files("jdssarrow.datamodel.schema").joinpath("jdssdm.xsd")
            return _lxml_etree.XMLSchema(_lxml_etree.fromstring(xsd.read_bytes()))
        except Exception:  # pragma: no cover - missing/invalid schema is non-fatal
            return None

    # ------------------------------------------------------------------ encode
    def encode(self, message: JdssMessage) -> bytes:
        data = message.model_dump(mode="json")
        root = self._builder.Element("JdssMessage")
        header = self._builder.SubElement(root, "header")
        _dict_to_elements(self._builder, header, data["header"])
        body = self._builder.SubElement(root, "body")
        _dict_to_elements(self._builder, body, data["body"])
        if _HAVE_LXML:
            return _lxml_etree.tostring(root, xml_declaration=True, encoding="UTF-8")
        return _std_etree.tostring(root, encoding="UTF-8", xml_declaration=True)

    # ------------------------------------------------------------------ decode
    def decode(self, raw: bytes) -> JdssMessage:
        if _HAVE_LXML:
            root = _lxml_etree.fromstring(raw)
            if self._schema is not None and not self._schema.validate(root):
                raise ValueError(f"JDSSDM schema validation failed: {self._schema.error_log}")
        else:
            root = _std_etree.fromstring(raw)
        payload: dict = {}
        for section in root:
            tag = section.tag.split("}")[-1]
            payload[tag] = _elements_to_dict(section)
        return message_from_dict(payload)
