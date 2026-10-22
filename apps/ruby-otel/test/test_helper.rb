# frozen_string_literal: true

require "json"
require "minitest/autorun"
require "rack/test"

require_relative "../app"

class RecordingTelemetry
  attr_reader :calls

  def initialize
    @calls = []
  end

  def in_span(name, attributes: {})
    @calls << { method: :in_span, name: name, attributes: attributes }
    yield
  end

  def log(severity:, body:, attributes: {})
    @calls << { method: :log, severity: severity, body: body, attributes: attributes }
  end

  def record_request(route:, outcome:)
    @calls << { method: :record_request, route: route, outcome: outcome }
  end

  def record_exception(exception)
    @calls << { method: :record_exception, exception: exception }
  end
end

class RecordingTravelAgent
  attr_reader :destinations

  def initialize(result:)
    @result = result
    @destinations = []
  end

  def call(destination:)
    @destinations << destination
    @result
  end
end

class RubyOtelDemoTest < Minitest::Test
  include Rack::Test::Methods

  def app
    RubyOtelDemo::App
  end

  def setup
    @telemetry = RecordingTelemetry.new
    @travel_agent = RecordingTravelAgent.new(
      result: {
        destination: "Kyoto, Japan",
        weather: { temperature_c: 18.4, weather: "partly cloudy" },
        hotels: [{ name: "Demo Hotel" }],
        recommendation: "Pack a light jacket and stay near the city center."
      }
    )
    app.set :telemetry, @telemetry
    app.set :travel_agent, @travel_agent
    header "Host", "localhost"
  end

  private

  def parsed_body
    JSON.parse(last_response.body)
  end
end
