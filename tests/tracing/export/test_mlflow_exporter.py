from dataclasses import dataclass
from unittest.mock import MagicMock

import pandas as pd
from opentelemetry.sdk.trace import ReadableSpan

from mlflow.tracing.export.mlflow import MLflowSpanExporter
from mlflow.tracing.types.constant import (
    MAX_CHARS_IN_TRACE_INFO_ATTRIBUTE,
    TRUNCATION_SUFFIX,
    TraceMetadataKey,
)
from mlflow.tracing.types.wrapper import MLflowSpanWrapper


@dataclass
class _MockSpanContext:
    trace_id: str
    span_id: str


def test_export():
    trace_id = "trace_id"
    otel_span_root = ReadableSpan(
        name="test_span",
        context=_MockSpanContext(trace_id, "span_id_1"),
        parent=None,
        attributes={
            "key1": "value1",
        },
        start_time=0,
        end_time=4_000_000,  # nano seconds
    )

    otel_span_child_1 = ReadableSpan(
        name="test_span_child_1",
        context=_MockSpanContext(trace_id, "span_id_2"),
        parent=otel_span_root.context,
        attributes={
            "key2": "value2",
        },
        start_time=1_000_000,
        end_time=2_000_000,
    )

    otel_span_child_2 = ReadableSpan(
        name="test_span_child_2",
        context=_MockSpanContext(trace_id, "span_id_3"),
        parent=otel_span_root.context,
        start_time=2_000_000,
        end_time=3_000_000,
    )

    mock_client = MagicMock()
    exporter = MLflowSpanExporter(mock_client)

    # Export the first child span -> no client call
    exporter.export([MLflowSpanWrapper(otel_span_child_1)])
    assert mock_client.log_trace.call_count == 0

    # Export the second child span -> no client call
    exporter.export([MLflowSpanWrapper(otel_span_child_2)])
    assert mock_client.log_trace.call_count == 0

    # Export the root span -> client call
    root_span = MLflowSpanWrapper(otel_span_root)
    root_span.set_inputs({"input1": "very long input" * 100})
    root_span.set_outputs({"output1": "very long output" * 100})
    exporter.export([root_span])

    assert mock_client.log_trace.call_count == 1
    client_call_args = mock_client.log_trace.call_args[0][0]

    # Trace info should inherit fields from the root span
    trace_info = client_call_args.trace_info
    assert trace_info.request_id == trace_id
    assert trace_info.timestamp_ms == 0
    assert trace_info.execution_time_ms == 4
    assert trace_info.request_metadata[TraceMetadataKey.NAME] == "test_span"

    # Inputs and outputs in TraceInfo attributes should be serialized and truncated
    inputs = trace_info.request_metadata[TraceMetadataKey.INPUTS]
    assert inputs.startswith('{"input1": "very long input')
    assert inputs.endswith(TRUNCATION_SUFFIX)
    assert len(inputs) == MAX_CHARS_IN_TRACE_INFO_ATTRIBUTE

    outputs = trace_info.request_metadata[TraceMetadataKey.OUTPUTS]
    assert outputs.startswith('{"output1": "very long output')
    assert outputs.endswith(TRUNCATION_SUFFIX)
    assert len(outputs) == MAX_CHARS_IN_TRACE_INFO_ATTRIBUTE

    # All 3 spans should be in the logged trace data
    assert len(client_call_args.trace_data.spans) == 3

    # Spans should be cleared from the aggregator
    assert len(exporter._trace_manager._traces) == 0


def test_serialize_inputs_outputs():
    exporter = MLflowSpanExporter(MagicMock())
    assert exporter._serialize_inputs_outputs({"x": 1, "y": 2}) == '{"x": 1, "y": 2}'
    # Truncate long inputs
    assert len(exporter._serialize_inputs_outputs({"x": "very long input" * 100})) == 300
    # non-JSON-serializable inputs
    assert (
        exporter._serialize_inputs_outputs({"input": pd.DataFrame({"x": [1], "y": [2]})})
        == "{'input':    x  y\n0  1  2}"
    )