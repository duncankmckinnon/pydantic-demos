# frozen_string_literal: true

require "json"

require_relative "travel_clients"

module RubyOtelDemo
  class TravelAgent
    AGENT_NAME = "travel-planner"

    def initialize(gateway:, weather:, hotels:, telemetry:)
      @gateway = gateway
      @weather = weather
      @hotels = hotels
      @telemetry = telemetry
    end

    def call(destination:)
      telemetry.in_span(
        "invoke_agent #{AGENT_NAME}",
        attributes: {
          "gen_ai.operation.name" => "invoke_agent",
          "gen_ai.agent.name" => AGENT_NAME,
          "gen_ai.workflow.name" => "live_travel_recommendation",
          "travel.destination" => destination
        }
      ) do
        plan = model_span { gateway.plan(destination: destination) }
        tool_calls = plan.fetch(:tool_calls)
        weather_result = execute_weather(tool_call(tool_calls, "get_weather"), destination)
        hotel_result = execute_hotels(tool_call(tool_calls, "find_hotels"), weather_result)
        recommendation = model_span do
          gateway.recommend(
            destination: destination,
            weather: weather_result,
            hotels: hotel_result,
            tool_calls: tool_calls
          )
        end

        {
          destination: destination,
          weather: weather_result,
          hotels: hotel_result,
          recommendation: recommendation.fetch(:content)
        }
      end
    end

    private

    attr_reader :gateway, :weather, :hotels, :telemetry

    def model_span
      telemetry.in_span(
        "chat #{gateway.model}",
        attributes: {
          "gen_ai.operation.name" => "chat",
          "gen_ai.provider.name" => "openai",
          "gen_ai.request.model" => gateway.model
        }
      ) do |span|
        response = yield
        span.set_attribute("gen_ai.response.id", response.fetch(:id))
        span.set_attribute("gen_ai.response.model", response.fetch(:model))
        span.set_attribute("gen_ai.response.finish_reasons", [response.fetch(:finish_reason)])
        span.set_attribute("gen_ai.usage.input_tokens", response.fetch(:input_tokens))
        span.set_attribute("gen_ai.usage.output_tokens", response.fetch(:output_tokens))
        response
      end
    end

    def execute_weather(call, fallback_destination)
      arguments = call.fetch(:arguments)
      destination = arguments.fetch("destination", fallback_destination)
      tool_span(call, arguments) { weather.current(destination: destination) }
    end

    def execute_hotels(call, weather_result)
      arguments = call.fetch(:arguments)
      tool_span(call, arguments) do
        hotels.nearby(
          latitude: weather_result.fetch(:latitude),
          longitude: weather_result.fetch(:longitude)
        )
      end
    end

    def tool_span(call, arguments)
      telemetry.in_span(
        "execute_tool #{call.fetch(:name)}",
        attributes: {
          "gen_ai.operation.name" => "execute_tool",
          "gen_ai.tool.name" => call.fetch(:name),
          "gen_ai.tool.type" => "extension",
          "gen_ai.tool.call.id" => call.fetch(:id),
          "gen_ai.tool.call.arguments" => JSON.generate(arguments)
        }
      ) do |span|
        result = yield
        span.set_attribute("gen_ai.tool.call.result", JSON.generate(result))
        result
      end
    end

    def tool_call(tool_calls, name)
      tool_calls.find { |call| call.fetch(:name) == name } ||
        raise(ExternalServiceError, "model did not call #{name}")
    end
  end
end
