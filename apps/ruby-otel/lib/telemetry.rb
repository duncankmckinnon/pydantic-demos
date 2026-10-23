# frozen_string_literal: true

require "json"
require "opentelemetry/sdk"
require "opentelemetry-exporter-otlp"
require "opentelemetry-metrics-sdk"
require "opentelemetry-exporter-otlp-metrics"
require "opentelemetry-logs-sdk"
require "opentelemetry-exporter-otlp-logs"
require "opentelemetry/instrumentation/sinatra"

module RubyOtelDemo
  module Telemetry
    SERVICE_NAME = "ruby-otel-demo"
    SERVICE_VERSION = "0.1.0"
    DEPLOYMENT_ENVIRONMENT = "local"
    REQUEST_COUNTER_NAME = "ruby_otel.requests"
    REQUEST_COUNTER_UNIT = "{request}"

    class << self
      def start!(span_processor: nil, metric_reader: nil, log_record_processor: nil, instrument: true)
        mutex.synchronize do
          return if @started

          OpenTelemetry::SDK.configure do |config|
            config.resource = resource
            config.add_span_processor(span_processor) if span_processor
            config.add_metric_reader(metric_reader) if metric_reader
            config.add_log_record_processor(log_record_processor) if log_record_processor
            config.use("OpenTelemetry::Instrumentation::Sinatra") if instrument
          end

          @tracer_provider = OpenTelemetry.tracer_provider
          @meter_provider = OpenTelemetry.meter_provider
          @logger_provider = OpenTelemetry.logger_provider
          @tracer = @tracer_provider.tracer(SERVICE_NAME, SERVICE_VERSION)
          @request_counter = @meter_provider
                             .meter(SERVICE_NAME, version: SERVICE_VERSION)
                             .create_counter(
                               REQUEST_COUNTER_NAME,
                               unit: REQUEST_COUNTER_UNIT,
                               description: "Demo HTTP requests"
                             )
          @logger = @logger_provider.logger(name: SERVICE_NAME, version: SERVICE_VERSION)
          @started = true
          register_at_exit
        end
      end

      def shutdown!
        mutex.synchronize do
          return unless @started

          [@tracer_provider, @meter_provider, @logger_provider].each do |provider|
            provider.force_flush
            provider.shutdown
          end
        ensure
          @started = false
          @tracer_provider = nil
          @meter_provider = nil
          @logger_provider = nil
          @tracer = nil
          @request_counter = nil
          @logger = nil
        end
      end

      def in_span(name, attributes: {}, &block)
        ensure_started!
        @tracer.in_span(name, attributes: attributes, &block)
      end

      def log(severity:, body:, attributes: {})
        ensure_started!
        @logger.on_emit(
          severity_text: severity,
          body: body,
          attributes: attributes
        )
      end

      def record_request(route:, outcome:)
        ensure_started!
        @request_counter.add(1, attributes: { "route" => route, "outcome" => outcome })
      end

      def record_evaluation(name:, passed:, explanation:, target:)
        ensure_started!
        @logger.on_emit(
          event_name: "gen_ai.evaluation.result",
          body: "evaluation: #{name}=#{passed ? 'True' : 'False'}",
          attributes: {
            "gen_ai.evaluation.name" => name,
            "gen_ai.evaluation.score.value" => passed ? 1.0 : 0.0,
            "gen_ai.evaluation.score.label" => passed ? "pass" : "fail",
            "gen_ai.evaluation.explanation" => explanation,
            "gen_ai.evaluation.target" => target,
            "gen_ai.evaluation.evaluator.source" => JSON.generate(
              name: "RubyOtelDemo::TravelEvaluators",
              arguments: {}
            ),
            "gen_ai.evaluation.evaluator.version" => SERVICE_VERSION
          }
        )
      end

      def record_exception(exception)
        ensure_started!
        span = OpenTelemetry::Trace.current_span
        span.record_exception(exception)
        span.status = OpenTelemetry::Trace::Status.error
      end

      private

      def ensure_started!
        raise "telemetry has not been started" unless @started
      end

      def mutex
        @mutex ||= Mutex.new
      end

      def register_at_exit
        return if @at_exit_registered

        at_exit { shutdown! }
        @at_exit_registered = true
      end

      def resource
        OpenTelemetry::SDK::Resources::Resource.create(
          "service.name" => SERVICE_NAME,
          "service.version" => SERVICE_VERSION,
          "deployment.environment.name" => DEPLOYMENT_ENVIRONMENT
        )
      end
    end
  end
end
