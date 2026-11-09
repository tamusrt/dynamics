"""Checks of the trend step: python -m pytest tools/weather/test_weather_trend.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from Weather_to_JARVIS_inputs import trend_to_year  # noqa: E402


def test_clear_trend_is_applied_and_noise_is_not():
    # temperature rises 0.1 per year with small wobble; wind_east is pure alternating noise
    dated = [(2000 + y, (10 + 0.1 * y + (0.01 if y % 2 else -0.01), 1e5, 0.3, (1 if y % 2 else -1), 0.0)) for y in range(30)]
    shifted, slopes = trend_to_year(dated, 2030)
    assert abs(slopes[0] - 0.1) < 1e-3
    assert slopes[3] == 0.0 and slopes[4] == 0.0
    assert all(abs(s[0] - 13.0) < 0.05 for s in shifted)  # every year moved onto the 2030 line
