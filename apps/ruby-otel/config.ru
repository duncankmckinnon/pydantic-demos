# frozen_string_literal: true

require_relative "lib/telemetry"
require_relative "lib/travel_clients"
require_relative "lib/travel_agent"
require_relative "app"

RubyOtelDemo::Telemetry.start!
RubyOtelDemo::App.set :telemetry, RubyOtelDemo::Telemetry
RubyOtelDemo::App.set(
  :travel_agent,
  RubyOtelDemo::TravelAgent.new(
    gateway: RubyOtelDemo::GatewayClient.new(
      api_key: ENV["PYDANTIC_AI_GATEWAY_API_KEY"],
      base_url: ENV.fetch(
        "PYDANTIC_AI_GATEWAY_BASE_URL",
        RubyOtelDemo::GatewayClient::DEFAULT_BASE_URL
      ),
      model: ENV.fetch("PYDANTIC_AI_GATEWAY_MODEL", "gpt-5.6")
    ),
    weather: RubyOtelDemo::WeatherClient.new,
    hotels: RubyOtelDemo::HotelClient.new,
    telemetry: RubyOtelDemo::Telemetry
  )
)

run RubyOtelDemo::App
