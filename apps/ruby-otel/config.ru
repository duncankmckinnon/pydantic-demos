# frozen_string_literal: true

require_relative "lib/telemetry"
require_relative "lib/travel_clients"
require_relative "lib/travel_agent"
require_relative "lib/ruby_llm_setup"
require_relative "app"

RubyOtelDemo::Telemetry.start!
RubyLLM::OpenTelemetry.enable
RubyOtelDemo::RubyLlmSetup.configure!(
  api_key: ENV["PYDANTIC_AI_GATEWAY_API_KEY"],
  base_url: ENV.fetch(
    "PYDANTIC_AI_GATEWAY_BASE_URL",
    RubyOtelDemo::RubyLlmSetup::DEFAULT_BASE_URL
  )
)
RubyOtelDemo::App.set :telemetry, RubyOtelDemo::Telemetry
RubyOtelDemo::App.set(
  :travel_agent,
  RubyOtelDemo::TravelAgent.new(
    model: ENV.fetch("PYDANTIC_AI_GATEWAY_MODEL", RubyOtelDemo::RubyLlmSetup::DEFAULT_MODEL),
    weather: RubyOtelDemo::WeatherClient.new,
    hotels: RubyOtelDemo::HotelClient.new(
      endpoint: ENV.fetch("NOMINATIM_ENDPOINT", RubyOtelDemo::HotelClient::ENDPOINT)
    ),
    telemetry: RubyOtelDemo::Telemetry
  )
)

run RubyOtelDemo::App
