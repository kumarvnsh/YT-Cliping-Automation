"""Remove internal production labels from public-facing descriptions."""
import re

def description(text):
    lines=[]
    for line in str(text or '').splitlines():
        if re.match(r'^\s*Shotsnip\s+(?:capture\b|intro and edit\b|breakdown and commentary\b)',line,re.I):
            continue
        lines.append(line)
    return re.sub(r'\n{3,}','\n\n','\n'.join(lines)).strip()
