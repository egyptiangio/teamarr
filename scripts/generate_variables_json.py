#!/usr/bin/env python3
"""
Generate data/variables.json from docs/variable_reference.md

This script parses the markdown documentation and creates a centralized
JSON schema for all template variables.
"""

import json
import re
from pathlib import Path

def parse_markdown_table(lines):
    """Parse markdown table rows into variable dictionaries"""
    variables = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith('|---') or line.startswith('| Variable'):
            continue
        if not line.startswith('|'):
            continue

        # Split by | and clean
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 6:
            continue

        # Extract variable name from `{variable_name}`
        var_match = re.search(r'\{([^}]+)\}', parts[1])
        if not var_match:
            continue

        var_name = var_match.group(1)
        description = parts[2]
        source = parts[3]
        example = parts[4].strip('"')
        notes = parts[5]

        variables.append({
            'name': var_name,
            'description': description,
            'source': source,
            'example': example,
            'notes': notes
        })

    return variables

def determine_type(example, name):
    """Infer variable type from example and name"""
    if name.startswith('is_') or name.startswith('has_'):
        return 'boolean'
    if example.lower() in ['true', 'false']:
        return 'boolean'
    try:
        float(example.replace(',', ''))
        return 'number'
    except:
        return 'string'

def determine_availability(notes, source):
    """Determine when variable is available"""
    notes_lower = notes.lower()
    source_lower = source.lower()

    if 'same-day only' in notes_lower or 'same day only' in notes_lower:
        return 'same_day_only'
    if 'final only' in notes_lower:
        return 'final_only'
    if 'playoff' in notes_lower and 'only' in notes_lower:
        return 'playoff_only'
    if 'live' in notes_lower and 'only' in notes_lower:
        return 'live_only'
    return 'always'

def determine_sports(notes, name):
    """Determine applicable sports"""
    notes_lower = notes.lower()
    name_lower = name.lower()

    sports = []

    # Sport-specific prefixes
    if name_lower.startswith('basketball_'):
        return ['basketball']
    if name_lower.startswith('football_'):
        return ['football']
    if name_lower.startswith('hockey_'):
        return ['hockey']
    if name_lower.startswith('baseball_'):
        return ['baseball']

    # Check notes
    if 'college only' in notes_lower:
        return ['basketball', 'football']  # Common college sports
    if 'pro leagues' in notes_lower or 'pro only' in notes_lower:
        sports = ['basketball', 'football', 'hockey', 'baseball']
    if 'nfl' in notes_lower:
        return ['football']
    if 'nba' in notes_lower:
        return ['basketball']
    if 'nhl' in notes_lower:
        return ['hockey']
    if 'mlb' in notes_lower:
        return ['baseball']

    # Default: all sports
    return ['basketball', 'football', 'hockey', 'baseball']

def parse_variable_reference(md_path):
    """Parse variable_reference.md and extract all variables"""
    with open(md_path, 'r') as f:
        content = f.read()

    # Split by sections (###)
    sections = content.split('\n###')

    all_variables = []
    current_category = None
    current_icon = None

    for section in sections:
        lines = section.strip().split('\n')
        if not lines:
            continue

        # First line is the category header
        header = lines[0].strip()

        # Extract icon and category name
        icon_match = re.search(r'^([\U0001F300-\U0001F9FF]+)\s*(.+)$', header)
        if icon_match:
            current_icon = icon_match.group(1)
            current_category = icon_match.group(2).strip()
        else:
            current_category = header
            current_icon = '📝'

        # Parse table in this section
        table_lines = [l for l in lines if l.strip().startswith('|')]
        if not table_lines:
            continue

        variables = parse_markdown_table(table_lines)

        for var in variables:
            var['category'] = current_category
            var['icon'] = current_icon
            var['type'] = determine_type(var['example'], var['name'])
            var['availability'] = determine_availability(var['notes'], var['source'])
            var['sports'] = determine_sports(var['notes'], var['name'])

            # Clean up
            del var['notes']

            all_variables.append(var)

    return all_variables

def main():
    # Paths
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    md_path = repo_root / 'docs' / 'variable_reference.md'
    output_path = repo_root / 'data' / 'variables.json'

    print(f'Parsing {md_path}...')

    if not md_path.exists():
        print(f'Error: {md_path} not found')
        return 1

    # Parse markdown
    variables = parse_variable_reference(md_path)

    print(f'Found {len(variables)} variables')

    # Create output directory
    output_path.parent.mkdir(exist_ok=True)

    # Create JSON schema
    schema = {
        'version': '1.0.0',
        'generated': '2025-11-21',
        'total_variables': len(variables),
        'variables': variables
    }

    # Write JSON
    with open(output_path, 'w') as f:
        json.dump(schema, f, indent=2)

    print(f'Wrote {len(variables)} variables to {output_path}')

    # Print summary by category
    categories = {}
    for var in variables:
        cat = var['category']
        categories[cat] = categories.get(cat, 0) + 1

    print('\nVariables by category:')
    for cat, count in sorted(categories.items()):
        print(f'  {cat}: {count}')

    return 0

if __name__ == '__main__':
    exit(main())
