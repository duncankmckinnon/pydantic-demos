# frozen_string_literal: true

require_relative "test_helper"
require_relative "../lib/telemetry"
require_relative "../lib/travel_agent"

class FakeRubyLlmChat
  Message = Data.define(:content)

  attr_reader :instructions, :prompt, :tool_options, :tools

  def initialize(content: <<~TEXT.strip, tool_names: %w[get_weather find_hotels])
    Weather: partly cloudy, 18.4°C.
    1. Hotel Granvia Kyoto — connected to Kyoto Station.
    2. Kyoto Century Hotel — another nearby option.
  TEXT
    @content = content
    @tool_names = tool_names
  end

  def with_instructions(instructions)
    @instructions = instructions
    self
  end

  def with_tools(*tools)
    @tools = tools
    self
  end

  def with_tool_options(**options)
    @tool_options = options
    self
  end

  def ask(prompt)
    @prompt = prompt
    named_tools = tools.to_h { |tool| [tool.name, tool] }
    @tool_names.each { |name| named_tools.fetch(name).execute(destination: "Kyoto, Japan") }
    Message.new(@content)
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
    @chat = FakeRubyLlmChat.new
    @weather = FakeWeatherClient.new
    @hotels = FakeHotelClient.new
  end

  def teardown
    RubyOtelDemo::Telemetry.shutdown!
    ENV["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = @previous_capture_mode
  end

  def test_travel_agent_runs_ruby_llm_tools_and_captures_agent_input_and_output
    agent = build_agent

    result = agent.call(destination: "Kyoto, Japan")

    assert_equal "Kyoto, Japan", result.fetch(:destination)
    assert_includes result.fetch(:recommendation), "Hotel Granvia Kyoto"
    assert_equal ["Kyoto, Japan"], @weather.destinations
    assert_equal [{ latitude: 35.0116, longitude: 135.7681 }], @hotels.coordinates
    assert_equal "Plan a hotel stay in Kyoto, Japan.", @chat.prompt
    assert_equal %w[find_hotels get_weather], @chat.tools.map(&:name).sort
    assert_equal({ choice: :required, calls: :many, concurrency: false }, @chat.tool_options)

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
    @chat = FakeRubyLlmChat.new(content: <<~TEXT.strip)
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
    @chat = FakeRubyLlmChat.new(content: <<~TEXT.strip)
      Weather: partly cloudy, 18.4°C.
      1. Imaginary Palace — a luxurious choice.
    TEXT

    build_agent.call(destination: "Kyoto, Japan")

    evaluation = evaluation("hotel_grounding")
    assert_equal "fail", evaluation.attributes.fetch("gen_ai.evaluation.score.label")
    assert_includes evaluation.attributes.fetch("gen_ai.evaluation.explanation"), "Imaginary Palace"
  end

  def test_weather_grounding_fails_when_the_response_omits_fetched_weather
    @chat = FakeRubyLlmChat.new(content: "1. Hotel Granvia Kyoto — connected to Kyoto Station.")

    build_agent.call(destination: "Kyoto, Japan")

    evaluation = evaluation("weather_grounding")
    assert_equal "fail", evaluation.attributes.fetch("gen_ai.evaluation.score.label")
  end

  def test_no_booking_claims_fails_for_unsupported_price_or_availability
    @chat = FakeRubyLlmChat.new(content: <<~TEXT.strip)
      Weather: partly cloudy, 18.4°C.
      1. Hotel Granvia Kyoto — rooms are available for $250 per night.
    TEXT

    build_agent.call(destination: "Kyoto, Japan")

    evaluation = evaluation("no_booking_claims")
    assert_equal "fail", evaluation.attributes.fetch("gen_ai.evaluation.score.label")
    assert_includes evaluation.attributes.fetch("gen_ai.evaluation.explanation"), "price or availability"
  end

  def test_missing_required_tool_call_is_an_external_failure_and_marks_agent_span_error
    @chat = FakeRubyLlmChat.new(tool_names: ["get_weather"])

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

    root = @span_exporter.finished_spans.find { |span| span.name == "invoke_agent travel-planner" }
    refute root.attributes.key?("gen_ai.input.messages")
    refute root.attributes.key?("gen_ai.output.messages")
  end

  private

  def build_agent
    RubyOtelDemo::TravelAgent.new(
      model: "gpt-5.6-terra",
      weather: @weather,
      hotels: @hotels,
      telemetry: RubyOtelDemo::Telemetry,
      chat_factory: ->(**) { @chat }
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
