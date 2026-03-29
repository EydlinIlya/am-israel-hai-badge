from __future__ import annotations

from pathlib import Path

from .time_fmt import format_duration

_BADGE_DIR = Path(__file__).resolve().parents[2] / "badges"

_SVG_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" width="420" height="120" viewBox="0 0 420 120">
  <!-- Base background -->
  <rect width="420" height="120" fill="#f8f9fa"/>

  <!-- Ghost border -->
  <rect width="420" height="120" fill="none" stroke="#e4beba" stroke-width="1" rx="2" opacity="0.3"/>

  <!-- Left red accent bar -->
  <rect x="0" y="0" width="4" height="120" fill="#b10726"/>

  <!-- Title: split-color -->
  <text font-family="'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
    font-size="16" font-weight="800">
    <tspan x="18" y="28" fill="#191c1d">Deliver </tspan><tspan fill="#b10726">No Matter What</tspan>
  </text>

  <!-- Commit count (top right) -->
  <text x="406" y="28"
    font-family="'Segoe UI', system-ui, sans-serif"
    font-size="10" font-weight="600" fill="#5b403d" text-anchor="end"
    letter-spacing="1">{commits} contributions / 30d</text>

  <!-- Subtitle -->
  <text x="18" y="46"
    font-family="'Segoe UI', system-ui, sans-serif"
    font-size="13" font-weight="500" fill="#5b403d">Time spent in bomb shelter in:</text>

  <!-- Shelter stats dark block -->
  <rect x="4" y="54" width="416" height="66" fill="#2e3132"/>

  <!-- Column dividers -->
  <rect x="144" y="62" width="1" height="50" fill="#e4beba" opacity="0.15"/>
  <rect x="284" y="62" width="1" height="50" fill="#e4beba" opacity="0.15"/>

  <!-- Col 1: last 24 hours -->
  <text x="74" y="78"
    font-family="'Segoe UI', system-ui, sans-serif"
    font-size="10" font-weight="600" fill="#e4beba" text-anchor="middle"
    letter-spacing="0.5">Last 24 hours</text>
  <text x="74" y="104"
    font-family="'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
    font-size="22" font-weight="700" fill="#f0f1f2" text-anchor="middle">{h24}</text>

  <!-- Col 2: last 7 days -->
  <text x="214" y="78"
    font-family="'Segoe UI', system-ui, sans-serif"
    font-size="10" font-weight="600" fill="#e4beba" text-anchor="middle"
    letter-spacing="0.5">Last 7 days</text>
  <text x="214" y="104"
    font-family="'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
    font-size="22" font-weight="700" fill="#f0f1f2" text-anchor="middle">{d7}</text>

  <!-- Col 3: last 30 days -->
  <text x="354" y="78"
    font-family="'Segoe UI', system-ui, sans-serif"
    font-size="10" font-weight="600" fill="#e4beba" text-anchor="middle"
    letter-spacing="0.5">Last 30 days</text>
  <text x="354" y="104"
    font-family="'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
    font-size="22" font-weight="700" fill="#f0f1f2" text-anchor="middle">{d30}</text>
</svg>"""


def generate_badge(seconds_24h: float, seconds_7d: float, seconds_30d: float, commits_30d: int = 0) -> str:
    """Generate SVG badge content."""
    return _SVG_TEMPLATE.format(
        h24=format_duration(seconds_24h),
        d7=format_duration(seconds_7d),
        d30=format_duration(seconds_30d),
        commits=commits_30d,
    )


def write_badge(seconds_24h: float, seconds_7d: float, seconds_30d: float, commits_30d: int = 0) -> Path:
    """Generate and write SVG badge to badges/shelter.svg."""
    _BADGE_DIR.mkdir(parents=True, exist_ok=True)
    path = _BADGE_DIR / "shelter.svg"
    svg = generate_badge(seconds_24h, seconds_7d, seconds_30d, commits_30d)
    path.write_text(svg, encoding="utf-8")
    return path
