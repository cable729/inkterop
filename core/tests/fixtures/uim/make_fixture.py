"""Generate the synthetic UIM v3.1.0 fixture in this directory.

Uses Wacom's own Apache-2.0 reference implementation (PyPI:
universal-ink-library, imports as `uim`) as the encoder, so the fixture
is by construction a spec-conformant file - our reader is tested against
Wacom's writer, not against itself.

Run:
    cd core
    uv run --with universal-ink-library \
        python tests/fixtures/uim/make_fixture.py

Content: two strokes with known geometry, per-point size, pressure,
timestamp, azimuth and altitude sensor channels, plus document
properties. Stroke 1 is a diagonal wave (blue), stroke 2 a horizontal
line (red, marked "Highlighter" in the brush name).
"""
from __future__ import annotations

import math
from pathlib import Path

from uim.codec.parser.base import SupportedFormats
from uim.codec.writer.encoder.encoder_3_1_0 import UIMEncoder310
from uim.model.base import UUIDIdentifier
from uim.model.ink import InkModel, InkTree
from uim.model.inkdata.brush import BrushPolygonUri, VectorBrush
from uim.model.inkdata.strokes import LayoutMask, Spline, Stroke, Style
from uim.model.inkinput.inputdata import (
    Environment, InkInputProvider, InkInputType, InkSensorMetricType,
    InkSensorType, InputContext, InputDevice, SensorChannel,
    SensorChannelsContext, SensorContext, Unit, unit2unit,
)
from uim.model.inkinput.sensordata import InkState, SensorData
from uim.model.semantics.node import StrokeGroupNode, StrokeNode

HERE = Path(__file__).parent

# Known-shape ink: (x, y, size, pressure) per point, plus 10ms timestamps.
STROKE_1 = [  # diagonal wave
    (t * 10.0 + 20.0, 100.0 + 30.0 * math.sin(t * 0.7), 2.0 + t * 0.2,
     0.30 + 0.05 * t)
    for t in range(10)
]
STROKE_2 = [  # horizontal line, constant size
    (20.0 + t * 25.0, 200.0, 8.0, 0.5)
    for t in range(5)
]


def build_model() -> InkModel:
    model = InkModel(version=SupportedFormats.UIM_VERSION_3_1_0.value)
    model.unit_scale_factor = 1.0
    model.properties.append(("Title", "inkterop synthetic fixture"))
    model.properties.append(("License", "CC0-1.0"))

    env = Environment()
    env.properties.append(("app.id", "inkterop-make-fixture"))
    model.input_configuration.environments.append(env)

    provider = InkInputProvider(input_type=InkInputType.PEN)
    model.input_configuration.ink_input_providers.append(provider)
    device = InputDevice()
    device.properties.append(("dev.model", "synthetic"))
    model.input_configuration.devices.append(device)

    channels = [
        SensorChannel(channel_type=InkSensorType.X,
                      metric=InkSensorMetricType.LENGTH, resolution=1.0,
                      ink_input_provider_id=provider.id,
                      input_device_id=device.id),
        SensorChannel(channel_type=InkSensorType.Y,
                      metric=InkSensorMetricType.LENGTH, resolution=1.0,
                      ink_input_provider_id=provider.id,
                      input_device_id=device.id),
        SensorChannel(channel_type=InkSensorType.TIMESTAMP,
                      metric=InkSensorMetricType.TIME, resolution=1000.0,
                      precision=0, ink_input_provider_id=provider.id,
                      input_device_id=device.id),
        SensorChannel(channel_type=InkSensorType.PRESSURE,
                      metric=InkSensorMetricType.NORMALIZED, resolution=1.0,
                      channel_min=0.0, channel_max=1.0,
                      ink_input_provider_id=provider.id,
                      input_device_id=device.id),
        SensorChannel(channel_type=InkSensorType.AZIMUTH,
                      metric=InkSensorMetricType.ANGLE, resolution=1.0,
                      channel_min=-math.pi, channel_max=math.pi,
                      ink_input_provider_id=provider.id,
                      input_device_id=device.id),
        SensorChannel(channel_type=InkSensorType.ALTITUDE,
                      metric=InkSensorMetricType.ANGLE, resolution=1.0,
                      channel_min=0.0, channel_max=math.pi / 2,
                      ink_input_provider_id=provider.id,
                      input_device_id=device.id),
    ]
    scc = SensorChannelsContext(channels=channels,
                                ink_input_provider_id=provider.id,
                                input_device_id=device.id)
    sensor_context = SensorContext()
    sensor_context.add_sensor_channels_context(scc)
    model.input_configuration.sensor_contexts.append(sensor_context)
    input_context = InputContext(environment_id=env.id,
                                 sensor_context_id=sensor_context.id)
    model.input_configuration.input_contexts.append(input_context)

    pen_brush = VectorBrush(
        "app://inkterop/vector-brush/FixturePen",
        [BrushPolygonUri("will://brush/3.0/shape/Circle?precision=20&radius=1",
                         0.0)])
    hl_brush = VectorBrush(
        "app://inkterop/vector-brush/FixtureHighlighter",
        [BrushPolygonUri("will://brush/3.0/shape/Circle?precision=20&radius=1",
                         0.0)])
    model.brushes.add_vector_brush(pen_brush)
    model.brushes.add_vector_brush(hl_brush)

    root = StrokeGroupNode(UUIDIdentifier.id_generator())
    model.ink_tree = InkTree()
    model.ink_tree.root = root

    for points, brush, rgba in (
            (STROKE_1, pen_brush, (0.0, 0.2, 0.8, 1.0)),
            (STROKE_2, hl_brush, (1.0, 0.0, 0.0, 0.5))):
        sd = SensorData(UUIDIdentifier.id_generator(),
                        input_context_id=input_context.id,
                        state=InkState.PLANE)
        sd.add_data(channels[0],
                    [unit2unit(Unit.DIP, Unit.M, p[0]) for p in points])
        sd.add_data(channels[1],
                    [unit2unit(Unit.DIP, Unit.M, p[1]) for p in points])
        sd.add_timestamp_data(channels[2],
                              [1735689600.0 + 0.01 * i
                               for i in range(len(points))])
        sd.add_data(channels[3], [p[3] for p in points])
        sd.add_data(channels[4], [0.5] * len(points))
        sd.add_data(channels[5], [1.0] * len(points))
        model.sensor_data.add(sd)

        spline_data: list[float] = []
        for x, y, size, _ in points:
            spline_data.extend((x, y, size))
        mask = LayoutMask.X.value | LayoutMask.Y.value | LayoutMask.SIZE.value
        style = Style(brush_uri=brush.name)
        style.path_point_properties.red = rgba[0]
        style.path_point_properties.green = rgba[1]
        style.path_point_properties.blue = rgba[2]
        style.path_point_properties.alpha = rgba[3]
        stroke = Stroke(sid=UUIDIdentifier.id_generator(),
                        sensor_data_id=sd.id, sensor_data_offset=0,
                        spline=Spline(layout_mask=mask, data=spline_data),
                        style=style)
        root.add(StrokeNode(stroke))
    return model


if __name__ == "__main__":
    model = build_model()
    out = HERE / "two-strokes-pressure.uim"
    out.write_bytes(UIMEncoder310().encode(model))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
