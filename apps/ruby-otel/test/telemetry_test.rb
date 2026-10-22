# frozen_string_literal: true

require_relative "test_helper"
require_relative "../lib/telemetry"

class CountingSpanExporter < OpenTelemetry::SDK::Trace::Export::InMemorySpanExporter
  attr_reader :flush_count, :shutdown_count

  def initialize
    super
    @flush_count = 0
    @shutdown_count = 0
  end

  def force_flush(timeout: nil)
    @flush_count += 1
    super
  end

  def shutdown(timeout: nil)
    @shutdown_count += 1
    super
  end
end

class CountingMetricReader < OpenTelemetry::SDK::Metrics::Export::InMemoryMetricPullExporter
  attr_reader :flush_count, :shutdown_count

  def initialize
    super
    @flush_count = 0
    @shutdown_count = 0
  end

  def force_flush(timeout: nil)
    @flush_count += 1
    pull
  end

  def shutdown(timeout: nil)
    @shutdown_count += 1
    OpenTelemetry::SDK::Metrics::Export::SUCCESS
  end
end

class CountingLogExporter < OpenTelemetry::SDK::Logs::Export::InMemoryLogRecordExporter
  attr_reader :flush_count, :shutdown_count

  def initialize
    super
    @flush_count = 0
    @shutdown_count = 0
  end

  def force_flush(timeout: nil)
    @flush_count += 1
    super
  end

  def shutdown(timeout: nil)
    @shutdown_count += 1
    super
  end
end

class TelemetryTest < Minitest::Test
  def setup
    @span_exporter = CountingSpanExporter.new
    @metric_reader = CountingMetricReader.new
    @log_exporter = CountingLogExporter.new

    RubyOtelDemo::Telemetry.start!(
      span_processor: OpenTelemetry::SDK::Trace::Export::SimpleSpanProcessor.new(@span_exporter),
      metric_reader: @metric_reader,
      log_record_processor: OpenTelemetry::SDK::Logs::Export::SimpleLogRecordProcessor.new(@log_exporter),
      instrument: false
    )
  end

  def teardown
    RubyOtelDemo::Telemetry.shutdown!
  end

  def test_in_span_creates_named_child_with_attributes
    RubyOtelDemo::Telemetry.in_span("ruby-otel.work", attributes: { "demo.operation" => "work" }) {}

    span = @span_exporter.finished_spans.fetch(0)
    assert_equal "ruby-otel.work", span.name
    assert_equal({ "demo.operation" => "work" }, span.attributes)
  end

  def test_log_uses_active_trace_context_and_structured_attributes
    RubyOtelDemo::Telemetry.in_span("ruby-otel.work") do
      RubyOtelDemo::Telemetry.log(
        severity: "info",
        body: "ruby work completed",
        attributes: { "demo.operation" => "work" }
      )
    end

    record = @log_exporter.emitted_log_records.fetch(0)
    assert_equal "ruby work completed", record.body
    assert_equal "info", record.severity_text
    assert_equal({ "demo.operation" => "work" }, record.attributes)
    refute_equal OpenTelemetry::Trace::INVALID_TRACE_ID, record.trace_id
    refute_equal OpenTelemetry::Trace::INVALID_SPAN_ID, record.span_id
  end

  def test_request_counter_adds_one_with_bounded_attributes
    RubyOtelDemo::Telemetry.record_request(route: "/work", outcome: "success")
    @metric_reader.pull

    metric = @metric_reader.metric_snapshots.find { |snapshot| snapshot.name == "ruby_otel.requests" }
    data_point = metric.data_points.fetch(0)
    assert_equal "{request}", metric.unit
    assert_equal 1, data_point.value
    assert_equal({ "route" => "/work", "outcome" => "success" }, data_point.attributes)
  end

  def test_record_exception_sets_current_span_error_status
    RubyOtelDemo::Telemetry.in_span("ruby-otel.error") do
      begin
        raise RuntimeError, "sensitive internal failure"
      rescue StandardError => error
        RubyOtelDemo::Telemetry.record_exception(error)
      end
    end

    span = @span_exporter.finished_spans.fetch(0)
    assert_equal OpenTelemetry::Trace::Status::ERROR, span.status.code
    assert_equal "exception", span.events.fetch(0).name
    refute_includes span.attributes.values, "sensitive internal failure"
  end

  def test_shutdown_force_flushes_and_is_idempotent
    RubyOtelDemo::Telemetry.shutdown!
    RubyOtelDemo::Telemetry.shutdown!

    assert_equal 1, @span_exporter.flush_count
    assert_equal 1, @span_exporter.shutdown_count
    assert_equal 1, @metric_reader.flush_count
    assert_equal 1, @metric_reader.shutdown_count
    assert_equal 1, @log_exporter.flush_count
    assert_equal 1, @log_exporter.shutdown_count
  end

  def test_start_does_not_raise_when_otlp_endpoint_is_unavailable
    RubyOtelDemo::Telemetry.shutdown!
    endpoint_variables = %w[
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
      OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
      OTEL_EXPORTER_OTLP_LOGS_ENDPOINT
    ]
    previous = endpoint_variables.to_h { |name| [name, ENV[name]] }
    endpoint_variables.each { |name| ENV[name] = "http://127.0.0.1:1/#{name.downcase}" }

    RubyOtelDemo::Telemetry.start!(instrument: false)
    RubyOtelDemo::Telemetry.in_span("collector-unavailable") {}
    assert true
  ensure
    RubyOtelDemo::Telemetry.shutdown!
    previous&.each { |name, value| value.nil? ? ENV.delete(name) : ENV[name] = value }
  end
end
