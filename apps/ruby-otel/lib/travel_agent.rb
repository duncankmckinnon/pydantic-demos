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
      user_message = text_message("user", "Plan a hotel stay in #{destination}.")
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
        capture_messages(agent_span, "gen_ai.input.messages", [user_message])

        plan = model_span(input_messages: [user_message]) { gateway.plan(destination: destination) }
        tool_calls = plan.fetch(:tool_calls)
        weather_result = execute_weather(tool_call(tool_calls, "get_weather"), destination)
        hotel_result = execute_hotels(tool_call(tool_calls, "find_hotels"), weather_result)
        recommendation_input = [
          user_message,
          tool_call_message(tool_calls),
          tool_result_message(tool_calls, weather_result, hotel_result)
        ]
        recommendation = model_span(input_messages: recommendation_input) do
          gateway.recommend(
            destination: destination,
            weather: weather_result,
            hotels: hotel_result,
            tool_calls: tool_calls,
            previous_response_id: plan.fetch(:id)
          )
        end
        capture_messages(agent_span, "gen_ai.output.messages", output_messages(recommendation))

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

    def model_span(input_messages:)
      telemetry.in_span(
        "chat #{gateway.model}",
        attributes: {
          "gen_ai.operation.name" => "chat",
          "gen_ai.provider.name" => "openai",
          "gen_ai.request.model" => gateway.model
        }
      ) do |span|
        capture_system_instructions(span)
        capture_messages(span, "gen_ai.input.messages", input_messages)
        response = yield
        span.set_attribute("gen_ai.response.id", response.fetch(:id))
        span.set_attribute("gen_ai.response.model", response.fetch(:model))
        span.set_attribute("gen_ai.response.finish_reasons", [response.fetch(:finish_reason)])
        span.set_attribute("gen_ai.usage.input_tokens", response.fetch(:input_tokens))
        span.set_attribute("gen_ai.usage.output_tokens", response.fetch(:output_tokens))
        capture_messages(span, "gen_ai.output.messages", output_messages(response))
        response
      end
    end

    def capture_system_instructions(span)
      capture_messages(
        span,
        "gen_ai.system_instructions",
        [{ type: "text", content: GatewayClient::SYSTEM_PROMPT }]
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

    def tool_call_message(tool_calls)
      {
        role: "assistant",
        parts: tool_calls.map do |call|
          {
            type: "tool_call",
            id: call.fetch(:id),
            name: call.fetch(:name),
            arguments: call.fetch(:arguments)
          }
        end
      }
    end

    def tool_result_message(tool_calls, weather_result, hotel_result)
      {
        role: "tool",
        parts: tool_calls.map do |call|
          result = call.fetch(:name) == "get_weather" ? weather_result : hotel_result
          { type: "tool_call_response", id: call.fetch(:id), response: result }
        end
      }
    end

    def output_messages(response)
      parts = if response[:content]
                [{ type: "text", content: response.fetch(:content) }]
              else
                tool_call_message(response.fetch(:tool_calls)).fetch(:parts)
              end
      [{ role: "assistant", parts: parts }]
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
