# frozen_string_literal: true

require_relative "test_helper"
require_relative "../lib/telemetry"
require_relative "../lib/travel_agent"

class FakeGatewayClient
  attr_reader :model, :requests

  def initialize(content: <<~TEXT.strip)
    Weather: partly cloudy, 18.4°C.
    1. Hotel Granvia Kyoto — connected to Kyoto Station.
    2. Kyoto Century Hotel — another nearby option.
  TEXT
    @model = "gpt-5.6"
    @requests = []
    @content = content
  end

  def plan(destination:)
    @requests << { operation: :plan, destination: destination }
    {
      id: "resp-plan",
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

  def recommend(destination:, weather:, hotels:, tool_calls:, previous_response_id:)
    @requests << {
      operation: :recommend,
      destination: destination,
      weather: weather,
      hotels: hotels,
      tool_calls: tool_calls,
      previous_response_id: previous_response_id
    }
    {
      id: "resp-final",
      model: "gpt-5.6-20261001",
      finish_reason: "stop",
      input_tokens: 310,
      output_tokens: 88,
      content: @content
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

  def initialize(listings: nil)
    @coordinates = []
    @listings = listings || [
      { name: "Hotel Granvia Kyoto", classification_stars: "5", website: "https://example.test/granvia" },
      { name: "Kyoto Century Hotel", classification_stars: "4", website: nil }
    ]
  end

  def nearby(latitude:, longitude:)
    @coordinates << { latitude: latitude, longitude: longitude }
    @listings
  end
end

class TravelAgentTest < Minitest::Test
  def setup
    @previous_capture_mode = ENV["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"]
    ENV["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "span_only"
    @span_exporter = OpenTelemetry::SDK::Trace::Export::InMemorySpanExporter.new
    @log_exporter = OpenTelemetry::SDK::Logs::Export::InMemoryLogRecordExporter.new
    RubyOtelDemo::Telemetry.start!(
      span_processor: OpenTelemetry::SDK::Trace::Export::SimpleSpanProcessor.new(@span_exporter),
      log_record_processor: OpenTelemetry::SDK::Logs::Export::SimpleLogRecordProcessor.new(@log_exporter),
      instrument: false
    )
    @gateway = FakeGatewayClient.new
    @weather = FakeWeatherClient.new
    @hotels = FakeHotelClient.new
  end

  def teardown
    RubyOtelDemo::Telemetry.shutdown!
    ENV["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = @previous_capture_mode
  end

  def test_travel_agent_builds_nested_agent_model_and_live_tool_trace
    agent = build_agent

    result = agent.call(destination: "Kyoto, Japan")

    assert_equal "Kyoto, Japan", result.fetch(:destination)
    assert_includes result.fetch(:recommendation), "Hotel Granvia Kyoto"
    assert_equal ["Kyoto, Japan"], @weather.destinations
    assert_equal [{ latitude: 35.0116, longitude: 135.7681 }], @hotels.coordinates
    assert_equal "resp-plan", @gateway.requests.fetch(1).fetch(:previous_response_id)

    spans = @span_exporter.finished_spans
    root = spans.find { |span| span.name == "invoke_agent travel-planner" }
    refute_nil root
    assert_equal "invoke_agent", root.attributes.fetch("gen_ai.operation.name")
    assert_equal "travel-planner", root.attributes.fetch("gen_ai.agent.name")
    assert_equal(
      "Plan a hotel stay in Kyoto, Japan.",
      JSON.parse(root.attributes.fetch("gen_ai.input.messages")).dig(0, "parts", 0, "content")
    )
    assert_equal(
      result.fetch(:recommendation),
      JSON.parse(root.attributes.fetch("gen_ai.output.messages")).dig(0, "parts", 0, "content")
    )

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

    planning_span, recommendation_span = model_spans
    assert_equal(
      [{ "role" => "user", "parts" => [{ "type" => "text", "content" => "Plan a hotel stay in Kyoto, Japan." }] }],
      JSON.parse(planning_span.attributes.fetch("gen_ai.input.messages"))
    )
    planning_output = JSON.parse(planning_span.attributes.fetch("gen_ai.output.messages"))
    assert_equal ["find_hotels", "get_weather"], planning_output.fetch(0).fetch("parts").map { |part| part.fetch("name") }.sort
    assert_includes recommendation_span.attributes.fetch("gen_ai.system_instructions"), "concise travel planning agent"
    recommendation_input = JSON.parse(recommendation_span.attributes.fetch("gen_ai.input.messages"))
    assert_equal ["assistant", "tool", "user"], recommendation_input.map { |message| message.fetch("role") }.sort
    recommendation_output = JSON.parse(recommendation_span.attributes.fetch("gen_ai.output.messages"))
    assert_equal result.fetch(:recommendation), recommendation_output.dig(0, "parts", 0, "content")

    tool_spans = child_spans.select { |span| span.name.start_with?("execute_tool") }
    assert tool_spans.all? { |span| span.attributes.fetch("gen_ai.operation.name") == "execute_tool" }
    assert_equal ["find_hotels", "get_weather"], tool_spans.map { |span| span.attributes.fetch("gen_ai.tool.name") }.sort
  rescue NameError => error
    flunk "travel agent is not implemented: #{error.message}"
  end

  def test_emits_four_passing_online_evaluations_on_the_agent_trace
    build_agent.call(destination: "Kyoto, Japan")

    evaluations = evaluation_records.to_h do |record|
      [record.attributes.fetch("gen_ai.evaluation.name"), record]
    end
    assert_equal %w[hotel_grounding no_booking_claims recommendation_count weather_grounding], evaluations.keys.sort
    assert evaluations.values.all? { |record| record.event_name == "gen_ai.evaluation.result" }
    assert evaluations.values.all? { |record| record.attributes.fetch("gen_ai.evaluation.score.value") == 1.0 }
    assert evaluations.values.all? { |record| record.attributes.fetch("gen_ai.evaluation.score.label") == "pass" }

    root = @span_exporter.finished_spans.find { |span| span.name == "invoke_agent travel-planner" }
    assert evaluations.values.all? { |record| record.trace_id == root.trace_id }
    assert evaluations.values.all? { |record| record.span_id == root.span_id }
  end

  def test_recommendation_count_fails_for_four_hotel_recommendations
    listings = (1..4).map { |number| { name: "Kyoto Hotel #{number}" } }
    @hotels = FakeHotelClient.new(listings: listings)
    @gateway = FakeGatewayClient.new(content: <<~TEXT.strip)
      Weather: partly cloudy, 18.4°C.
      1. Kyoto Hotel 1 — option one.
      2. Kyoto Hotel 2 — option two.
      3. Kyoto Hotel 3 — option three.
      4. Kyoto Hotel 4 — option four.
    TEXT

    build_agent.call(destination: "Kyoto, Japan")

    evaluation = evaluation("recommendation_count")
    assert_equal 0.0, evaluation.attributes.fetch("gen_ai.evaluation.score.value")
    assert_equal "fail", evaluation.attributes.fetch("gen_ai.evaluation.score.label")
    assert_includes evaluation.attributes.fetch("gen_ai.evaluation.explanation"), "4"
  end

  def test_hotel_grounding_fails_for_a_recommendation_not_returned_by_the_tool
    @gateway = FakeGatewayClient.new(content: <<~TEXT.strip)
      Weather: partly cloudy, 18.4°C.
      1. Imaginary Palace — a luxurious choice.
    TEXT

    build_agent.call(destination: "Kyoto, Japan")

    evaluation = evaluation("hotel_grounding")
    assert_equal "fail", evaluation.attributes.fetch("gen_ai.evaluation.score.label")
    assert_includes evaluation.attributes.fetch("gen_ai.evaluation.explanation"), "Imaginary Palace"
  end

  def test_weather_grounding_fails_when_the_response_omits_fetched_weather
    @gateway = FakeGatewayClient.new(content: "1. Hotel Granvia Kyoto — connected to Kyoto Station.")

    build_agent.call(destination: "Kyoto, Japan")

    evaluation = evaluation("weather_grounding")
    assert_equal "fail", evaluation.attributes.fetch("gen_ai.evaluation.score.label")
  end

  def test_no_booking_claims_fails_for_unsupported_price_or_availability
    @gateway = FakeGatewayClient.new(content: <<~TEXT.strip)
      Weather: partly cloudy, 18.4°C.
      1. Hotel Granvia Kyoto — rooms are available for $250 per night.
    TEXT

    build_agent.call(destination: "Kyoto, Japan")

    evaluation = evaluation("no_booking_claims")
    assert_equal "fail", evaluation.attributes.fetch("gen_ai.evaluation.score.label")
    assert_includes evaluation.attributes.fetch("gen_ai.evaluation.explanation"), "price or availability"
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

  def test_model_content_is_not_captured_without_explicit_opt_in
    ENV["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "no_content"

    build_agent.call(destination: "Kyoto, Japan")

    model_spans = @span_exporter.finished_spans.select { |span| span.name == "chat gpt-5.6" }
    root = @span_exporter.finished_spans.find { |span| span.name == "invoke_agent travel-planner" }
    refute root.attributes.key?("gen_ai.input.messages")
    refute root.attributes.key?("gen_ai.output.messages")
    assert model_spans.none? { |span| span.attributes.key?("gen_ai.input.messages") }
    assert model_spans.none? { |span| span.attributes.key?("gen_ai.output.messages") }
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

  def evaluation_records
    @log_exporter.emitted_log_records.select { |record| record.event_name == "gen_ai.evaluation.result" }
  end

  def evaluation(name)
    evaluation_records.find { |record| record.attributes["gen_ai.evaluation.name"] == name } ||
      flunk("missing #{name} evaluation")
  end
end
