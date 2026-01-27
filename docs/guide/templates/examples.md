---
title: Examples
parent: Templates
grand_parent: User Guide
nav_order: 3
---

# Template Examples

Community-contributed templates to get you started.

{: .note }
These templates use [game-thumbs](../settings/advanced#game-thumbs) for dynamic program artwork. Replace `<game-thumbs-base-url>` with your game-thumbs instance URL or a hosted version.

## Community Templates

### Full-Featured Templates by @jesmannstlPanda

Comprehensive templates with pregame, postgame, and idle content. Features dynamic artwork via game-thumbs.

#### Team Template

For team-based channels with persistent channel assignments.

- Full pregame/postgame/idle content
- Conditional descriptions based on game status
- Dynamic artwork for all states
- Gracenote-style categories

[Download Team Template](../../assets/templates/team-template-jesmannstlpanda.json){: .btn .btn-primary }

#### Event Template

For event-based channels that appear only during games.

- Pregame and postgame content
- No idle content (channels only exist during events)
- Dynamic channel logos with broadcast network badges
- Gracenote-style categories

[Download Event Template](../../assets/templates/event-template-jesmannstlpanda.json){: .btn .btn-primary }

---

## Using Downloaded Templates

1. Download the template JSON file
2. Open the file and replace `<game-thumbs-base-url>` with your game-thumbs URL:
   - Self-hosted: `http://your-server:port`
   - Hosted options: See [game-thumbs documentation](game-thumbs#hosted-options)
3. In Teamarr, go to **Templates** and click **Import**
4. Select your modified JSON file

---

## Contributing Templates

Have a template you'd like to share? Join the Dispatcharr Discord and share it in the Teamarr channel.
