"""Per-point stroke channels.

X/Y are implicit (every stroke has them); channels carry the optional
per-point data. Struct-of-arrays: a stroke stores `channels[Channel] ->
list[float]` with one value per point.

Units contract (what readers must normalize TO):
- PRESSURE: 0.0-1.0 (0 = no contact reported, 1 = max sensor value)
- TILT_AZIMUTH: radians, 0 = +x axis, counterclockwise in page space
- TILT_ALTITUDE: radians, pi/2 = perpendicular to surface
- SPEED: source units/second (source-specific magnitude; comparable only
  within a document)
- WIDTH: rendered stroke width at this point, in the page's source units
  (the "device already computed it" channel - never re-derive from pressure
  when this is present)
- ALPHA: 0.0-1.0 opacity at this point
- TIMESTAMP: seconds since stroke start
"""
from __future__ import annotations

from enum import Enum


class Channel(str, Enum):
    PRESSURE = "pressure"
    TILT_AZIMUTH = "tilt_azimuth"
    TILT_ALTITUDE = "tilt_altitude"
    SPEED = "speed"
    WIDTH = "width"
    ALPHA = "alpha"
    TIMESTAMP = "timestamp"


#: Declared value range per channel (None = unbounded/source-specific).
CHANNEL_RANGE: dict[Channel, tuple[float, float] | None] = {
    Channel.PRESSURE: (0.0, 1.0),
    Channel.TILT_AZIMUTH: None,
    Channel.TILT_ALTITUDE: None,
    Channel.SPEED: None,
    Channel.WIDTH: None,
    Channel.ALPHA: (0.0, 1.0),
    Channel.TIMESTAMP: None,
}
