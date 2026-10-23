# frozen_string_literal: true

require "socket"
require "timeout"

require_relative "test_helper"
require_relative "../lib/ruby_llm_setup"
require_relative "../lib/telemetry"
require_relative "../lib/travel_agent"

class RubyLlmGatewayServer
  attr_reader :base_url, :requests

  def initialize(responses)
    @responses = responses
    @requests = []
    @server = TCPServer.new("127.0.0.1", 0)
    @base_url = "http://127.0.0.1:#{@server.local_address.ip_port}"
    @thread = Thread.new { serve }
  end

  def wait_for_requests(count, timeout: 3)
    Timeout.timeout(timeout) { sleep 0.01 until requests.length >= count }
  end

  def close
    @server.close
    @thread.join(1)
  end

  private

  def serve
    @responses.each do |response|
      socket = @server.accept
      method, target, = socket.gets.split
      headers = {}
      while (line = socket.gets)
        break if line == "\r\n"

        name, value = line.split(":", 2)
        headers[name.downcase] = value.strip
      end
      body = socket.read(headers.fetch("content-length", "0").to_i)
      requests << { method: method, target: target, headers: headers, body: body }
      payload = JSON.generate(response)
      socket.write(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n" \
        "Content-Length: #{payload.bytesize}\r\nConnection: close\r\n\r\n#{payload}"
      )
      socket.close
    end
  rescue IOError, Errno::EBADF
    nil
  end
end

class RubyLlmGatewayWeather
  def current(destination:)
    raise "unexpected destination" unless destination == "Kyoto, Japan"

    {
      location: "Kyoto, Japan", latitude: 35.0116, longitude: 135.7681,
      temperature_c: 18.4, weather: "partly cloudy", observed_at: "2026-10-21T12:00"
    }
  end
end

class RubyLlmGatewayHotels
  def nearby(latitude:, longitude:)
    raise "unexpected coordinates" unless [latitude, longitude] == [35.0116, 135.7681]

    [{ name: "Hotel Granvia Kyoto", classification_stars: "5" }]
  end
end

class RubyLlmGatewayTest < Minitest::Test
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
    RubyLLM::OpenTelemetry.enable
  end

  def teardown
    RubyLLM::OpenTelemetry.disable
    RubyOtelDemo::Telemetry.shutdown!
    ENV["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = @previous_capture_mode
    @server&.close
  end

  def test_ruby_llm_runs_the_responses_tool_loop_through_gateway_with_native_spans
    @server = RubyLlmGatewayServer.new([
      {
        id: "resp-plan", model: "gpt-5.6-20261001", status: "completed",
        usage: { input_tokens: 120, output_tokens: 24 },
        output: [
          {
            id: "fc-weather", type: "function_call", call_id: "call-weather",
            name: "get_weather", arguments: '{"destination":"Kyoto, Japan"}', status: "completed"
          },
          {
            id: "fc-hotels", type: "function_call", call_id: "call-hotels",
            name: "find_hotels", arguments: '{"destination":"Kyoto, Japan"}', status: "completed"
          }
        ]
      },
      {
        id: "resp-final", model: "gpt-5.6-20261001", status: "completed",
        usage: { input_tokens: 310, output_tokens: 88 },
        output: [{
          id: "msg-final", type: "message", role: "assistant", status: "completed",
          content: [{
            type: "output_text",
            text: "Weather: partly cloudy, 18.4°C.\n1. Hotel Granvia Kyoto — connected to Kyoto Station.",
            annotations: []
          }]
        }]
      }
    ])
    RubyOtelDemo::RubyLlmSetup.configure!(
      api_key: "pylf_test",
      base_url: "#{@server.base_url}/proxy/openai-responses"
    )
    RubyLLM.config.max_retries = 0
    agent = RubyOtelDemo::TravelAgent.new(
      model: "gpt-5.6-terra",
      weather: RubyLlmGatewayWeather.new,
      hotels: RubyLlmGatewayHotels.new,
      telemetry: RubyOtelDemo::Telemetry
    )

    result = agent.call(destination: "Kyoto, Japan")
    @server.wait_for_requests(2)

    assert_includes result.fetch(:recommendation), "Hotel Granvia Kyoto"
    assert_equal "Bearer pylf_test", @server.requests.fetch(0).fetch(:headers).fetch("authorization")
    assert_equal "/proxy/openai-responses/responses", @server.requests.fetch(0).fetch(:target)
    first_payload = JSON.parse(@server.requests.fetch(0).fetch(:body))
    assert_equal "gpt-5.6-terra", first_payload.fetch("model")
    assert_includes first_payload.fetch("instructions"), "must call both get_weather and find_hotels"
    assert_equal %w[find_hotels get_weather], first_payload.fetch("tools").map { |tool| tool.fetch("name") }.sort
    assert_equal "required", first_payload.fetch("tool_choice")
    second_payload = JSON.parse(@server.requests.fetch(1).fetch(:body))
    assert_equal 2, second_payload.fetch("input").count { |item| item["type"] == "function_call_output" }

    spans = @span_exporter.finished_spans
    root = spans.find { |span| span.name == "invoke_agent travel-planner" }
    child_spans = spans.select { |span| span.parent_span_id == root.span_id }
    assert_equal ["chat gpt-5.6-terra", "chat gpt-5.6-terra"],
                 child_spans.select { |span| span.name.start_with?("chat ") }.map(&:name)
    tools = child_spans.select { |span| span.name.start_with?("execute_tool ") }
    assert_equal ["find_hotels", "get_weather"], tools.map { |span| span.attributes.fetch("gen_ai.tool.name") }.sort
    assert tools.all? { |span| span.attributes.key?("gen_ai.tool.call.arguments") }
    assert tools.all? { |span| span.attributes.key?("gen_ai.tool.call.result") }
  rescue KeyError, NameError, NoMethodError => error
    flunk "RubyLLM Gateway integration is incomplete: #{error.message}"
  end

  def test_gateway_configuration_requires_a_key_and_defaults_a_blank_base_url
    error = assert_raises(ArgumentError) do
      RubyOtelDemo::RubyLlmSetup.configure!(api_key: nil)
    end
    assert_equal "PYDANTIC_AI_GATEWAY_API_KEY is required", error.message

    RubyOtelDemo::RubyLlmSetup.configure!(api_key: "pylf_test", base_url: "")

    assert_equal "pylf_test", RubyLLM.config.openai_api_key
    assert_equal RubyOtelDemo::RubyLlmSetup::DEFAULT_BASE_URL, RubyLLM.config.openai_api_base
  end
end
