"""XMLTV generation utilities.

Converts Programme dataclasses to XMLTV format.
All times are output in the user's configured timezone.
"""

from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, tostring

from teamarr.core import Programme
from teamarr.utilities.tz import format_datetime_xmltv, to_user_tz


def _is_valid_xml_char(char: str) -> bool:
    """Return True if char is valid in XML 1.0."""
    codepoint = ord(char)
    return (
        codepoint == 0x9
        or codepoint == 0xA
        or codepoint == 0xD
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _sanitize_xml_text(text: str | None) -> str | None:
    """Remove characters that are invalid in XML 1.0.

    ElementTree escapes special characters, but it does not strip invalid codepoints.
    """
    if text is None:
        return None
    return "".join(char for char in text if _is_valid_xml_char(char))


def programmes_to_xmltv(
    programmes: list[Programme],
    channels: list[dict],
    generator_name: str = "Teamarr",
    generator_url: str | None = None,
) -> str:
    """Generate XMLTV XML from programmes.

    All times are converted to the user's configured timezone.

    Args:
        programmes: List of Programme objects
        channels: List of channel dicts with 'id', 'name', 'icon' keys
        generator_name: Generator info for XML header
        generator_url: Generator URL for XML header

    Returns:
        XMLTV XML string
    """
    root = Element("tv")
    root.set("generator-info-name", generator_name)
    if generator_url:
        root.set("generator-info-url", generator_url)

    # Add all channels first
    for channel in channels:
        _add_channel(root, channel)

    # Sort programmes by channel ID, then by start time (XMLTV standard convention)
    sorted_programmes = sorted(programmes, key=lambda p: (p.channel_id, p.start))
    for programme in sorted_programmes:
        _add_programme(root, programme)

    xml_str = tostring(root, encoding="unicode")
    return _prettify(xml_str)


def _add_channel(root: Element, channel: dict) -> None:
    """Add a channel element to the TV root."""
    chan_elem = SubElement(root, "channel")
    chan_elem.set("id", _sanitize_xml_text(channel["id"]) or "")

    name_elem = SubElement(chan_elem, "display-name")
    name_elem.text = _sanitize_xml_text(channel["name"])

    if channel.get("icon"):
        icon_elem = SubElement(chan_elem, "icon")
        icon_elem.set("src", _sanitize_xml_text(channel["icon"]) or "")


def _add_programme(root: Element, programme: Programme) -> None:
    """Add a programme element to the TV root."""
    from xml.etree.ElementTree import Comment

    prog_elem = SubElement(root, "programme")
    prog_elem.set("start", format_datetime_xmltv(programme.start))
    prog_elem.set("stop", format_datetime_xmltv(programme.stop))
    prog_elem.set("channel", _sanitize_xml_text(programme.channel_id) or "")

    # Add filler type comment for analysis (V1 compatibility)
    if programme.filler_type:
        prog_elem.append(Comment(f"teamarr:filler-{programme.filler_type}"))

    title_elem = SubElement(prog_elem, "title")
    title_elem.set("lang", "en")
    title_elem.text = _sanitize_xml_text(programme.title)

    if programme.subtitle:
        sub_elem = SubElement(prog_elem, "sub-title")
        sub_elem.set("lang", "en")
        sub_elem.text = _sanitize_xml_text(programme.subtitle)

    if programme.description:
        desc_elem = SubElement(prog_elem, "desc")
        desc_elem.set("lang", "en")
        desc_elem.text = _sanitize_xml_text(programme.description)

    # Add date tag if enabled (YYYYMMDD format in user's timezone)
    flags = programme.xmltv_flags or {}
    if flags.get("date"):
        date_elem = SubElement(prog_elem, "date")
        local_start = to_user_tz(programme.start)
        date_elem.text = local_start.strftime("%Y%m%d")

    # Add categories
    for cat in programme.categories:
        cat_elem = SubElement(prog_elem, "category")
        cat_elem.set("lang", "en")
        cat_elem.text = _sanitize_xml_text(cat)

    if programme.icon:
        icon_elem = SubElement(prog_elem, "icon")
        icon_elem.set("src", _sanitize_xml_text(programme.icon) or "")

    # Add video element if enabled (only for non-filler programmes)
    # Note: Teamarr does not detect actual stream resolution - this is user-configured
    video = programme.xmltv_video or {}
    if video.get("enabled") and not programme.filler_type:
        video_elem = SubElement(prog_elem, "video")
        if video.get("quality"):
            SubElement(video_elem, "quality").text = video["quality"]

    # Add new tag if enabled (only for non-filler programmes)
    if flags.get("new") and not programme.filler_type:
        SubElement(prog_elem, "new")

    # Add live tag if enabled (only for non-filler programmes)
    if flags.get("live") and not programme.filler_type:
        SubElement(prog_elem, "live")


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


def merge_xmltv_content(
    xmltv_contents: list[str],
    generator_name: str = "Teamarr",
    generator_url: str | None = None,
) -> str:
    """Merge multiple XMLTV content strings into one.

    Combines channels and programmes from multiple sources,
    removing duplicates by channel ID. Output follows XMLTV standard
    convention: all channels first, then programmes sorted by channel.

    Args:
        xmltv_contents: List of XMLTV XML strings
        generator_name: Generator info for XML header
        generator_url: Generator URL for XML header

    Returns:
        Merged XMLTV XML string
    """
    import xml.etree.ElementTree as ET

    root = Element("tv")
    root.set("generator-info-name", generator_name)
    if generator_url:
        root.set("generator-info-url", generator_url)

    seen_channels: set[str] = set()
    seen_programmes: set[tuple[str, str, str]] = set()  # (channel, start, stop)
    all_programmes: list[Element] = []

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

            # Collect programmes (skip duplicates, defer appending)
            for programme in source.findall("programme"):
                channel_id = programme.get("channel")
                start = programme.get("start")
                stop = programme.get("stop")

                key = (channel_id, start, stop)
                if key not in seen_programmes:
                    seen_programmes.add(key)
                    all_programmes.append(programme)

        except ET.ParseError:
            continue

    # Sort programmes by channel ID, then by start time (XMLTV standard convention)
    all_programmes.sort(key=lambda p: (p.get("channel", ""), p.get("start", "")))
    for programme in all_programmes:
        root.append(programme)

    xml_str = tostring(root, encoding="unicode")
    return _prettify(xml_str)
