# frozen_string_literal: true

require_relative "test_helper"
require_relative "../lib/telemetry"
require_relative "../lib/travel_agent"

class FakeGatewayClient
  attr_reader :model, :requests

  def initialize
    @model = "gpt-5.6"
    @requests = []
  end

  def plan(destination:)
    @requests << { operation: :plan, destination: destination }
    {
      id: "chatcmpl-plan",
      model: "gpt-5.6-20261001",
      finish_reason: "tool_calls",
      input_tokens: 120,
      output_tokens: 24,
      tool_calls: [
        { id: "call-weather", name: "get_weather", arguments: { "destination" => destination } },
        { id: "call-hotels", name: "find_hotels", arguments: { "destination" => destination } }
      ]
    }
  end

  def recommend(destination:, weather:, hotels:, tool_calls:)
    @requests << {
      operation: :recommend,
      destination: destination,
      weather: weather,
      hotels: hotels,
      tool_calls: tool_calls
    }
    {
      id: "chatcmpl-final",
      model: "gpt-5.6-20261001",
      finish_reason: "stop",
      input_tokens: 310,
      output_tokens: 88,
      content: "Pack a light jacket and consider Hotel Granvia Kyoto."
    }
  end
end

class FakeWeatherClient
  attr_reader :destinations

  def initialize
    @destinations = []
  end

  def current(destination:)
    @destinations << destination
    {
      location: "Kyoto, Japan",
      latitude: 35.0116,
      longitude: 135.7681,
      temperature_c: 18.4,
      weather: "partly cloudy",
      observed_at: "2026-10-21T12:00"
    }
  end
end

class FakeHotelClient
  attr_reader :coordinates

  def initialize
    @coordinates = []
  end

  def nearby(latitude:, longitude:)
    @coordinates << { latitude: latitude, longitude: longitude }
    [
      { name: "Hotel Granvia Kyoto", stars: "5", website: "https://example.test/granvia" },
      { name: "Kyoto Century Hotel", stars: "4", website: nil }
    ]
  end
end

class TravelAgentTest < Minitest::Test
  def setup
    @span_exporter = OpenTelemetry::SDK::Trace::Export::InMemorySpanExporter.new
    RubyOtelDemo::Telemetry.start!(
      span_processor: OpenTelemetry::SDK::Trace::Export::SimpleSpanProcessor.new(@span_exporter),
      instrument: false
    )
    @gateway = FakeGatewayClient.new
    @weather = FakeWeatherClient.new
    @hotels = FakeHotelClient.new
  end

  def teardown
    RubyOtelDemo::Telemetry.shutdown!
  end

  def test_travel_agent_builds_nested_agent_model_and_live_tool_trace
    agent = build_agent

    result = agent.call(destination: "Kyoto, Japan")

    assert_equal "Kyoto, Japan", result.fetch(:destination)
    assert_equal "Pack a light jacket and consider Hotel Granvia Kyoto.", result.fetch(:recommendation)
    assert_equal ["Kyoto, Japan"], @weather.destinations
    assert_equal [{ latitude: 35.0116, longitude: 135.7681 }], @hotels.coordinates

    spans = @span_exporter.finished_spans
    root = spans.find { |span| span.name == "invoke_agent travel-planner" }
    refute_nil root
    assert_equal "invoke_agent", root.attributes.fetch("gen_ai.operation.name")
    assert_equal "travel-planner", root.attributes.fetch("gen_ai.agent.name")

    child_spans = spans.reject { |span| span.equal?(root) }
    assert_equal [
      "chat gpt-5.6",
      "chat gpt-5.6",
      "execute_tool find_hotels",
      "execute_tool get_weather"
    ], child_spans.map(&:name).sort
    assert child_spans.all? { |span| span.parent_span_id == root.span_id }
    assert child_spans.all? { |span| span.hex_trace_id == root.hex_trace_id }

    model_spans = child_spans.select { |span| span.name == "chat gpt-5.6" }
    assert model_spans.all? { |span| span.attributes.fetch("gen_ai.operation.name") == "chat" }
    assert model_spans.all? { |span| span.attributes.fetch("gen_ai.provider.name") == "openai" }
    assert_equal [24, 88], model_spans.map { |span| span.attributes.fetch("gen_ai.usage.output_tokens") }.sort

    tool_spans = child_spans.select { |span| span.name.start_with?("execute_tool") }
    assert tool_spans.all? { |span| span.attributes.fetch("gen_ai.operation.name") == "execute_tool" }
    assert_equal ["find_hotels", "get_weather"], tool_spans.map { |span| span.attributes.fetch("gen_ai.tool.name") }.sort
  rescue NameError => error
    flunk "travel agent is not implemented: #{error.message}"
  end

  def test_missing_required_tool_call_is_an_external_failure_and_marks_agent_span_error
    gateway = @gateway
    gateway.define_singleton_method(:plan) do |destination:|
      super(destination: destination).merge(tool_calls: [
        { id: "call-weather", name: "get_weather", arguments: { "destination" => destination } }
      ])
    end

    error = assert_raises(RubyOtelDemo::ExternalServiceError) do
      build_agent.call(destination: "Kyoto, Japan")
    end

    assert_equal "model did not call find_hotels", error.message
    root = @span_exporter.finished_spans.find { |span| span.name == "invoke_agent travel-planner" }
    assert_equal OpenTelemetry::Trace::Status::ERROR, root.status.code
    assert_equal "exception", root.events.fetch(0).name
  end

  private

  def build_agent
    RubyOtelDemo::TravelAgent.new(
      gateway: @gateway,
      weather: @weather,
      hotels: @hotels,
      telemetry: RubyOtelDemo::Telemetry
    )
  end
end
