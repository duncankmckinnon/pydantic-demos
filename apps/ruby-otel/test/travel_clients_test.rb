# frozen_string_literal: true

require "cgi"
require "socket"
require "timeout"
require "uri"

require_relative "test_helper"

begin
  require_relative "../lib/travel_clients"
rescue LoadError
  nil
end

class ScriptedJsonServer
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
      @requests << { method: method, target: target, headers: headers, body: body }
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

class TravelClientsTest < Minitest::Test
  def teardown
    @server&.close
  end

  def test_weather_client_geocodes_destination_then_fetches_current_conditions
    @server = ScriptedJsonServer.new([
      {
        results: [{
          name: "Kyoto", country: "Japan", admin1: "Kyoto", latitude: 35.0116,
          longitude: 135.7681, timezone: "Asia/Tokyo"
        }]
      },
      {
        current: {
          time: "2026-10-21T12:00", temperature_2m: 18.4, apparent_temperature: 18.0,
          precipitation: 0.0, weather_code: 2, wind_speed_10m: 7.2
        },
        current_units: {
          temperature_2m: "°C", apparent_temperature: "°C", precipitation: "mm", wind_speed_10m: "km/h"
        }
      }
    ])

    client = RubyOtelDemo::WeatherClient.new(
      geocoding_base_url: @server.base_url,
      forecast_base_url: @server.base_url
    )
    result = client.current(destination: "Kyoto, Japan")
    @server.wait_for_requests(2)

    assert_equal(
      {
        location: "Kyoto, Japan",
        latitude: 35.0116,
        longitude: 135.7681,
        timezone: "Asia/Tokyo",
        observed_at: "2026-10-21T12:00",
        temperature_c: 18.4,
        apparent_temperature_c: 18.0,
        precipitation_mm: 0.0,
        wind_speed_kmh: 7.2,
        weather_code: 2,
        weather: "partly cloudy"
      },
      result
    )
    geocoding_query = CGI.parse(URI.parse(@server.requests.fetch(0).fetch(:target)).query)
    assert_equal ["Kyoto, Japan"], geocoding_query.fetch("name")
    forecast_query = CGI.parse(URI.parse(@server.requests.fetch(1).fetch(:target)).query)
    assert_equal ["35.0116"], forecast_query.fetch("latitude")
    assert_includes forecast_query.fetch("current").first, "weather_code"
  rescue NameError => error
    flunk "weather client is not implemented: #{error.message}"
  end

  def test_hotel_client_returns_named_live_osm_hotels_near_coordinates
    @server = ScriptedJsonServer.new([
      [
        {
          place_id: 1, lat: "35.01", lon: "135.76", display_name: "Hotel Granvia Kyoto, Kyoto, Japan",
          category: "tourism", type: "hotel", address: { hotel: "Hotel Granvia Kyoto", road: "Higashishiokoji" },
          extratags: { stars: "5", website: "https://granvia.example" }
        },
        {
          place_id: 2, lat: "35.02", lon: "135.77", display_name: "Kyoto Century Hotel, Kyoto, Japan",
          category: "tourism", type: "hotel", address: { hotel: "Kyoto Century Hotel", road: "Higashishiokoji" },
          extratags: nil
        }
      ]
    ])

    client = RubyOtelDemo::HotelClient.new(endpoint: "#{@server.base_url}/search")
    result = client.nearby(latitude: 35.0116, longitude: 135.7681)
    @server.wait_for_requests(1)

    assert_equal 2, result.length
    assert_equal "Hotel Granvia Kyoto", result.fetch(0).fetch(:name)
    assert_equal "5", result.fetch(0).fetch(:stars)
    assert_equal "Kyoto Century Hotel", result.fetch(1).fetch(:name)
    assert_equal "Higashishiokoji", result.fetch(1).fetch(:address)
    request = @server.requests.fetch(0)
    assert_equal "GET", request.fetch(:method)
    query = CGI.parse(URI.parse(request.fetch(:target)).query)
    assert_equal ["[hotel]"], query.fetch("q")
    assert_equal ["1"], query.fetch("bounded")
    assert_equal ["5"], query.fetch("limit")
    assert_match(/pydantic-demos-ruby-otel/, request.fetch(:headers).fetch("user-agent"))
  rescue NameError, NoMethodError, TypeError => error
    flunk "hotel client is not implemented: #{error.message}"
  end

  def test_gateway_client_sends_tools_and_parses_tool_calls_content_and_usage
    @server = ScriptedJsonServer.new([
      {
        id: "chatcmpl-plan", model: "gpt-5.6-20261001", usage: { prompt_tokens: 120, completion_tokens: 24 },
        choices: [{ finish_reason: "tool_calls", message: { role: "assistant", content: nil, tool_calls: [
          { id: "call-weather", type: "function", function: { name: "get_weather", arguments: '{"destination":"Kyoto, Japan"}' } },
          { id: "call-hotels", type: "function", function: { name: "find_hotels", arguments: '{"destination":"Kyoto, Japan"}' } }
        ] } }]
      },
      {
        id: "chatcmpl-final", model: "gpt-5.6-20261001", usage: { prompt_tokens: 310, completion_tokens: 88 },
        choices: [{ finish_reason: "stop", message: { role: "assistant", content: "Pack a light jacket." } }]
      }
    ])
    client = RubyOtelDemo::GatewayClient.new(
      api_key: "pylf_test",
      base_url: "#{@server.base_url}/proxy/chat/",
      model: "gpt-5.6"
    )

    plan = client.plan(destination: "Kyoto, Japan")
    final = client.recommend(
      destination: "Kyoto, Japan",
      weather: { weather: "partly cloudy" },
      hotels: [{ name: "Hotel Granvia Kyoto" }],
      tool_calls: plan.fetch(:tool_calls)
    )
    @server.wait_for_requests(2)

    assert_equal ["find_hotels", "get_weather"], plan.fetch(:tool_calls).map { |call| call.fetch(:name) }.sort
    assert_equal 24, plan.fetch(:output_tokens)
    assert_equal "Pack a light jacket.", final.fetch(:content)
    assert_equal 310, final.fetch(:input_tokens)
    assert_equal "Bearer pylf_test", @server.requests.fetch(0).fetch(:headers).fetch("authorization")
    assert_equal "/proxy/chat/chat/completions", @server.requests.fetch(0).fetch(:target)
    first_payload = JSON.parse(@server.requests.fetch(0).fetch(:body))
    assert_equal "gpt-5.6", first_payload.fetch("model")
    assert_equal ["find_hotels", "get_weather"], first_payload.fetch("tools").map { |tool| tool.dig("function", "name") }.sort
    second_payload = JSON.parse(@server.requests.fetch(1).fetch(:body))
    assert_equal 2, second_payload.fetch("messages").count { |message| message.fetch("role") == "tool" }
  rescue NameError => error
    flunk "gateway client is not implemented: #{error.message}"
  end
end
