from __future__ import annotations

from pathlib import Path

from .time_fmt import format_duration

_BADGE_DIR = Path(__file__).resolve().parents[2] / "badges"

_SVG_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" width="420" height="120" viewBox="0 0 420 120">
  <defs>
    <filter id="shadow" x="-4%" y="-4%" width="108%" height="116%">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#b91c1c" flood-opacity="0.18"/>
    </filter>
  </defs>
  <!-- outer card -->
  <rect width="420" height="120" rx="12" fill="#f9fafb" stroke="#dc2626" stroke-width="2.5" filter="url(#shadow)"/>
  <!-- red accent bar -->
  <rect x="0" y="0" width="8" height="120" rx="12" fill="#dc2626"/>
  <rect x="4" y="0" width="4" height="120" fill="#dc2626"/>
  <!-- title -->
  <text x="28" y="32" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="16" font-weight="700" fill="#dc2626">
    {shield} Time Spent in Shelter
  </text>
  <!-- subtitle -->
  <text x="28" y="52" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="11" font-weight="500" fill="#6b7280" font-style="italic">
    Deliver no matter what
  </text>
  <!-- divider -->
  <line x1="28" y1="64" x2="396" y2="64" stroke="#e5e7eb" stroke-width="1"/>
  <!-- stats -->
  <text x="70" y="90" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="13" fill="#374151" text-anchor="middle">
    <tspan font-weight="700" fill="#dc2626">24h</tspan>
    <tspan x="70" dy="16" font-size="15" font-weight="700" fill="#1f2937">{h24}</tspan>
  </text>
  <line x1="140" y1="74" x2="140" y2="108" stroke="#e5e7eb" stroke-width="1"/>
  <text x="210" y="90" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="13" fill="#374151" text-anchor="middle">
    <tspan font-weight="700" fill="#dc2626">7d</tspan>
    <tspan x="210" dy="16" font-size="15" font-weight="700" fill="#1f2937">{d7}</tspan>
  </text>
  <line x1="280" y1="74" x2="280" y2="108" stroke="#e5e7eb" stroke-width="1"/>
  <text x="350" y="90" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="13" fill="#374151" text-anchor="middle">
    <tspan font-weight="700" fill="#dc2626">30d</tspan>
    <tspan x="350" dy="16" font-size="15" font-weight="700" fill="#1f2937">{d30}</tspan>
  </text>
</svg>"""


def generate_badge(seconds_24h: float, seconds_7d: float, seconds_30d: float) -> str:
    """Generate SVG badge content."""
    return _SVG_TEMPLATE.format(
        shield="\U0001f6e1\ufe0f",  # 🛡️
        h24=format_duration(seconds_24h),
        d7=format_duration(seconds_7d),
        d30=format_duration(seconds_30d),
    )


def write_badge(seconds_24h: float, seconds_7d: float, seconds_30d: float) -> Path:
    """Generate and write SVG badge to badges/shelter.svg."""
    _BADGE_DIR.mkdir(parents=True, exist_ok=True)
    path = _BADGE_DIR / "shelter.svg"
    svg = generate_badge(seconds_24h, seconds_7d, seconds_30d)
    path.write_text(svg, encoding="utf-8")
    return path
