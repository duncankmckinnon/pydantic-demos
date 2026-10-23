# frozen_string_literal: true

require "json"
require "ruby_llm"

require_relative "travel_clients"
require_relative "travel_evaluators"

module RubyOtelDemo
  TravelRun = Struct.new(:destination, :weather, :hotels, keyword_init: true)

  class TravelTool < RubyLLM::Tool
    private

    def capture_tool_io(arguments, result)
      span = OpenTelemetry::Trace.current_span
      span.set_attribute("gen_ai.tool.call.arguments", JSON.generate(arguments))
      span.set_attribute("gen_ai.tool.call.result", JSON.generate(result))
      result
    end
  end

  class GetWeatherTool < TravelTool
    description "Get live current weather for a destination. Call this before find_hotels."
    parameter :destination, description: "City and country to check"

    def self.tool_name = "get_weather"

    def initialize(client:, run:)
      super()
      @client = client
      @run = run
    end

    def execute(destination:)
      result = @run.weather ||= @client.current(destination: @run.destination)
      capture_tool_io({ destination: destination }, result)
    end
  end

  class FindHotelsTool < TravelTool
    description "Find live nearby hotel listings from OpenStreetMap after checking the weather."
    parameter :destination, description: "City and country where hotels should be found"

    def self.tool_name = "find_hotels"

    def initialize(client:, run:)
      super()
      @client = client
      @run = run
    end

    def execute(destination:)
      weather = @run.weather
      unless weather
        return capture_tool_io(
          { destination: destination },
          { error: "Call get_weather for #{@run.destination} before find_hotels." }
        )
      end

      result = @run.hotels ||= @client.nearby(
        latitude: weather.fetch(:latitude),
        longitude: weather.fetch(:longitude)
      )
      capture_tool_io({ destination: destination }, result)
    end
  end

  class TravelAgent
    AGENT_NAME = "travel-planner"
    SYSTEM_PROMPT = <<~PROMPT.strip
      You are a concise travel planning agent. You must call both get_weather and find_hotels,
      in that order, before recommending up to three hotels. Base advice only on returned tool
      data. Never claim live prices, room availability, or that a booking has been made.
      Never claim ratings or reviews; classification stars are unverified OpenStreetMap metadata.
      State the fetched weather condition or Celsius temperature. Format each hotel recommendation
      as a numbered line beginning with its exact name from the find_hotels result.
    PROMPT

    def initialize(model:, weather:, hotels:, telemetry:, chat_factory: RubyLLM.method(:chat))
      @model = model
      @weather = weather
      @hotels = hotels
      @telemetry = telemetry
      @chat_factory = chat_factory
    end

    def call(destination:)
      prompt = "Plan a hotel stay in #{destination}."
      telemetry.in_span(
        "invoke_agent #{AGENT_NAME}",
        attributes: {
          "gen_ai.operation.name" => "invoke_agent",
          "gen_ai.agent.name" => AGENT_NAME,
          "gen_ai.workflow.name" => "live_travel_recommendation",
          "travel.destination" => destination
        }
      ) do |agent_span|
        capture_system_instructions(agent_span)
        capture_messages(agent_span, "gen_ai.input.messages", [text_message("user", prompt)])

        run = TravelRun.new(destination: destination)
        response = chat(run).ask(prompt)
        recommendation = response&.content.to_s
        raise ExternalServiceError, "model did not call get_weather" unless run.weather
        raise ExternalServiceError, "model did not call find_hotels" unless run.hotels
        raise ExternalServiceError, "Gateway response had no output text" if recommendation.empty?

        capture_messages(agent_span, "gen_ai.output.messages", [text_message("assistant", recommendation)])
        evaluate(recommendation, run.weather, run.hotels)

        {
          destination: destination,
          weather: run.weather,
          hotels: run.hotels,
          recommendation: recommendation
        }
      end
    rescue RubyLLM::Error => error
      raise ExternalServiceError, "Gateway request failed: #{error.class}"
    end

    private

    attr_reader :model, :weather, :hotels, :telemetry, :chat_factory

    def chat(run)
      chat_factory.call(
        model: model,
        provider: :openai,
        protocol: :responses,
        assume_model_exists: true
      ).with_instructions(SYSTEM_PROMPT)
        .with_tools(
          GetWeatherTool.new(client: weather, run: run),
          FindHotelsTool.new(client: hotels, run: run)
        )
        .with_tool_options(choice: :required, calls: :many, concurrency: false)
    end

    def capture_system_instructions(span)
      capture_messages(
        span,
        "gen_ai.system_instructions",
        [{ type: "text", content: SYSTEM_PROMPT }]
      )
    end

    def capture_messages(span, attribute, messages)
      return unless capture_message_content?

      span.set_attribute(attribute, JSON.generate(messages))
    end

    def capture_message_content?
      %w[true span_only span_and_event].include?(
        ENV.fetch("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "no_content").downcase
      )
    end

    def text_message(role, content)
      { role: role, parts: [{ type: "text", content: content }] }
    end

    def evaluate(response, weather_result, hotel_result)
      TravelEvaluators.evaluate(
        response: response,
        weather: weather_result,
        hotels: hotel_result
      ).each do |result|
        telemetry.record_evaluation(
          name: result.name,
          passed: result.passed,
          explanation: result.explanation,
          target: AGENT_NAME
        )
      end
    end
  end
end
