---
title: Examples
parent: Templates
grand_parent: User Guide
nav_order: 4
---

# Template Examples

Community-contributed templates to get you started quickly.

## Community Templates by @jesmannstlPanda

Production-ready templates designed to match real Gracenote EPG data as closely as possible, with enhancements like dynamic artwork via [Game Thumbs](../game-thumbs).

### Features

- **Gracenote-accurate formatting** - Titles, descriptions, and categories that match real EPG feeds
- **Dynamic artwork** - Matchup thumbnails generated on-the-fly showing team logos, scores, and game info
- **Full filler content** - Pregame, postgame, and idle programmes with detailed descriptions

### Team Template

[Download Team Template](../../assets/templates/team-template-jesmannstlpanda.json){: .btn .btn-primary }

### Event Template

[Download Event Template](../../assets/templates/event-template-jesmannstlpanda.json){: .btn .btn-primary }

---

## Game Thumbs Integration

These templates use Game Thumbs to generate dynamic programme artwork. Game Thumbs creates matchup images showing:

- Team logos for both teams
- Live scores during games
- Win/loss indicators for completed games
- Broadcast network badges
- Venue and time information

See [Game Thumbs](../game-thumbs) for setup instructions and hosted options.

---

## Using Downloaded Templates

1. Download the template JSON file
2. Open the file and replace `<game-thumbs-base-url>` with your Game Thumbs URL:
   - Self-hosted: `http://your-server:port`
   - Hosted options: See [Game Thumbs](../game-thumbs#hosted-instances)
3. In Teamarr, go to **Templates** and click **Import**
4. Select your modified JSON file

---

## Contributing Templates

Have a template you'd like to share? Join the Dispatcharr Discord and share it in the Teamarr channel.
