"""XMLTV generation utilities.

Converts Programme dataclasses to XMLTV format.
All times are output in the user's configured timezone.
"""

from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, tostring

from teamarr.core import Programme
from teamarr.utilities.tz import format_datetime_xmltv


def programmes_to_xmltv(
    programmes: list[Programme],
    channels: list[dict],
    generator_name: str = "Teamarr",
) -> str:
    """Generate XMLTV XML from programmes.

    All times are converted to the user's configured timezone.

    Args:
        programmes: List of Programme objects
        channels: List of channel dicts with 'id', 'name', 'icon' keys
        generator_name: Generator info for XML header

    Returns:
        XMLTV XML string
    """
    root = Element("tv")
    root.set("generator-info-name", generator_name)

    for channel in channels:
        _add_channel(root, channel)

    for programme in programmes:
        _add_programme(root, programme)

    xml_str = tostring(root, encoding="unicode")
    return _prettify(xml_str)


def _add_channel(root: Element, channel: dict) -> None:
    """Add a channel element to the TV root."""
    chan_elem = SubElement(root, "channel")
    chan_elem.set("id", channel["id"])

    name_elem = SubElement(chan_elem, "display-name")
    name_elem.text = channel["name"]

    if channel.get("icon"):
        icon_elem = SubElement(chan_elem, "icon")
        icon_elem.set("src", channel["icon"])


def _add_programme(root: Element, programme: Programme) -> None:
    """Add a programme element to the TV root."""
    prog_elem = SubElement(root, "programme")
    prog_elem.set("start", format_datetime_xmltv(programme.start))
    prog_elem.set("stop", format_datetime_xmltv(programme.stop))
    prog_elem.set("channel", programme.channel_id)

    title_elem = SubElement(prog_elem, "title")
    title_elem.set("lang", "en")
    title_elem.text = programme.title

    if programme.subtitle:
        sub_elem = SubElement(prog_elem, "sub-title")
        sub_elem.set("lang", "en")
        sub_elem.text = programme.subtitle

    if programme.description:
        desc_elem = SubElement(prog_elem, "desc")
        desc_elem.set("lang", "en")
        desc_elem.text = programme.description

    if programme.category:
        cat_elem = SubElement(prog_elem, "category")
        cat_elem.set("lang", "en")
        cat_elem.text = programme.category

    if programme.icon:
        icon_elem = SubElement(prog_elem, "icon")
        icon_elem.set("src", programme.icon)


def _prettify(xml_str: str) -> str:
    """Return pretty-printed XML string.

    Uses minidom for formatting, then removes extra blank lines
    that toprettyxml adds between elements.
    """
    dom = minidom.parseString(xml_str)
    pretty = dom.toprettyxml(indent="  ")
    # Remove blank lines (minidom adds whitespace-only text nodes)
    lines = [line for line in pretty.split("\n") if line.strip()]
    return "\n".join(lines)


def merge_xmltv_content(xmltv_contents: list[str]) -> str:
    """Merge multiple XMLTV content strings into one.

    Combines channels and programmes from multiple sources,
    removing duplicates by channel ID.

    Args:
        xmltv_contents: List of XMLTV XML strings

    Returns:
        Merged XMLTV XML string
    """
    import xml.etree.ElementTree as ET

    root = Element("tv")
    root.set("generator-info-name", "Teamarr v2")

    seen_channels: set[str] = set()
    seen_programmes: set[tuple[str, str, str]] = set()  # (channel, start, stop)

    for content in xmltv_contents:
        if not content or not content.strip():
            continue

        try:
            source = ET.fromstring(content)

            # Collect channels (skip duplicates)
            for channel in source.findall("channel"):
                channel_id = channel.get("id")
                if channel_id and channel_id not in seen_channels:
                    seen_channels.add(channel_id)
                    root.append(channel)

            # Collect programmes (skip duplicates)
            for programme in source.findall("programme"):
                channel_id = programme.get("channel")
                start = programme.get("start")
                stop = programme.get("stop")

                key = (channel_id, start, stop)
                if key not in seen_programmes:
                    seen_programmes.add(key)
                    root.append(programme)

        except ET.ParseError:
            continue

    xml_str = tostring(root, encoding="unicode")
    return _prettify(xml_str)
