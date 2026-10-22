# frozen_string_literal: true

require "json"
require "net/http"
require "openssl"
require "timeout"
require "uri"

module RubyOtelDemo
  class ExternalServiceError < StandardError; end

  class HttpJsonClient
    USER_AGENT = "pydantic-demos-ruby-otel/0.1"

    def initialize(open_timeout: 3, read_timeout: 12, transport: Net::HTTP)
      @open_timeout = open_timeout
      @read_timeout = read_timeout
      @transport = transport
    end

    def get(uri)
      request(uri, Net::HTTP::Get.new(uri))
    end

    def post_json(uri, payload, headers: {})
      request = Net::HTTP::Post.new(uri)
      request["Content-Type"] = "application/json"
      headers.each { |name, value| request[name] = value }
      request.body = JSON.generate(payload)
      request(uri, request)
    end

    def post_form(uri, fields)
      request = Net::HTTP::Post.new(uri)
      request.set_form_data(fields)
      request(uri, request)
    end

    private

    def request(uri, request)
      request["Accept"] = "application/json"
      request["User-Agent"] = USER_AGENT
      response = @transport.start(
        uri.host,
        uri.port,
        use_ssl: uri.scheme == "https",
        open_timeout: @open_timeout,
        read_timeout: @read_timeout
      ) { |http| http.request(request) }
      raise ExternalServiceError, "external service returned HTTP #{response.code}" unless response.is_a?(Net::HTTPSuccess)

      JSON.parse(response.body)
    rescue JSON::ParserError, IOError, SystemCallError, SocketError, Timeout::Error, OpenSSL::SSL::SSLError => error
      raise ExternalServiceError, "external service request failed: #{error.class}"
    end
  end

  class WeatherClient
    GEOCODING_BASE_URL = "https://geocoding-api.open-meteo.com"
    FORECAST_BASE_URL = "https://api.open-meteo.com"
    CURRENT_FIELDS = %w[
      temperature_2m apparent_temperature precipitation weather_code wind_speed_10m
    ].freeze

    WEATHER_CODES = {
      0 => "clear sky", 1 => "mainly clear", 2 => "partly cloudy", 3 => "overcast",
      45 => "fog", 48 => "depositing rime fog", 51 => "light drizzle", 53 => "drizzle",
      55 => "heavy drizzle", 61 => "light rain", 63 => "rain", 65 => "heavy rain",
      71 => "light snow", 73 => "snow", 75 => "heavy snow", 80 => "rain showers",
      81 => "rain showers", 82 => "heavy rain showers", 95 => "thunderstorm"
    }.freeze

    def initialize(
      http: HttpJsonClient.new,
      geocoding_base_url: GEOCODING_BASE_URL,
      forecast_base_url: FORECAST_BASE_URL
    )
      @http = http
      @geocoding_base_url = geocoding_base_url
      @forecast_base_url = forecast_base_url
    end

    def current(destination:)
      location = geocode(destination)
      latitude = Float(location.fetch("latitude"))
      longitude = Float(location.fetch("longitude"))
      forecast = http.get(uri(
        forecast_base_url,
        "/v1/forecast",
        latitude: latitude,
        longitude: longitude,
        current: CURRENT_FIELDS.join(","),
        timezone: location.fetch("timezone")
      ))
      raise TypeError, "forecast must be an object" unless forecast.is_a?(Hash)

      conditions = forecast.fetch("current")
      raise TypeError, "current conditions must be an object" unless conditions.is_a?(Hash)

      code = conditions.fetch("weather_code")

      {
        location: [location["name"], location["admin1"], location["country"]].compact.uniq.join(", "),
        latitude: latitude,
        longitude: longitude,
        timezone: location.fetch("timezone"),
        observed_at: conditions.fetch("time"),
        temperature_c: conditions.fetch("temperature_2m"),
        apparent_temperature_c: conditions.fetch("apparent_temperature"),
        precipitation_mm: conditions.fetch("precipitation"),
        wind_speed_kmh: conditions.fetch("wind_speed_10m"),
        weather_code: code,
        weather: WEATHER_CODES.fetch(code, "unknown conditions")
      }
    rescue KeyError, TypeError, ArgumentError => error
      raise ExternalServiceError, "weather response was incomplete: #{error.message}"
    end

    private

    attr_reader :http, :geocoding_base_url, :forecast_base_url

    def geocode(destination)
      response = http.get(uri(geocoding_base_url, "/v1/search", name: destination, count: 1, language: "en"))
      raise TypeError, "geocoding response must be an object" unless response.is_a?(Hash)

      results = response["results"]
      raise TypeError, "geocoding results must be an array" unless results.is_a?(Array)

      location = results.first || raise(ExternalServiceError, "destination was not found")
      raise TypeError, "geocoding result must be an object" unless location.is_a?(Hash)

      location
    end

    def uri(base_url, path, query)
      parsed = URI.join("#{base_url}/", path.delete_prefix("/"))
      parsed.query = URI.encode_www_form(query)
      parsed
    end
  end

  class HotelClient
    ENDPOINT = "https://nominatim.openstreetmap.org/search"
    MINIMUM_REQUEST_INTERVAL = 1.0

    class << self
      attr_accessor :last_request_at

      def request_mutex
        @request_mutex ||= Mutex.new
      end
    end

    def initialize(http: HttpJsonClient.new, endpoint: ENDPOINT, clock: Process.method(:clock_gettime), sleeper: Kernel.method(:sleep))
      @http = http
      @endpoint = URI(endpoint)
      @clock = clock
      @sleeper = sleeper
    end

    def nearby(latitude:, longitude:)
      uri = endpoint.dup
      uri.query = URI.encode_www_form(
        q: "[hotel]",
        format: "jsonv2",
        viewbox: viewbox(latitude, longitude),
        bounded: 1,
        limit: 5,
        addressdetails: 1,
        extratags: 1
      )
      response = rate_limited_get(uri)
      raise TypeError, "hotel response must be an array" unless response.is_a?(Array)

      response.filter_map { |place| hotel(place) }
    rescue KeyError, TypeError, ArgumentError => error
      raise ExternalServiceError, "hotel response was incomplete: #{error.message}"
    end

    private

    attr_reader :http, :endpoint, :clock, :sleeper

    def viewbox(latitude, longitude)
      [longitude - 0.025, latitude + 0.02, longitude + 0.025, latitude - 0.02].join(",")
    end

    def rate_limited_get(uri)
      self.class.request_mutex.synchronize do
        now = clock.call(Process::CLOCK_MONOTONIC)
        last_request_at = self.class.last_request_at
        sleeper.call(MINIMUM_REQUEST_INTERVAL - (now - last_request_at)) if last_request_at && now - last_request_at < MINIMUM_REQUEST_INTERVAL
        self.class.last_request_at = clock.call(Process::CLOCK_MONOTONIC)
        http.get(uri)
      end
    end

    def hotel(place)
      raise TypeError, "hotel entry must be an object" unless place.is_a?(Hash)

      address = place["address"] || {}
      extra = place["extratags"] || {}
      name = address["hotel"] || place.fetch("display_name").split(",").first
      return if name.to_s.empty?

      {
        name: name,
        classification_stars: extra["stars"],
        address: address["road"] || place.fetch("display_name"),
        website: extra["website"] || extra["contact:website"],
        latitude: Float(place.fetch("lat")),
        longitude: Float(place.fetch("lon")),
        source: "OpenStreetMap"
      }
    end
  end

  class GatewayClient
    DEFAULT_BASE_URL = "https://gateway-us.pydantic.dev/proxy/openai-responses"
    SYSTEM_PROMPT = <<~PROMPT.strip
      You are a concise travel planning agent. You must call both get_weather and find_hotels
      before recommending up to three hotels. Base advice only on returned tool data. Never
      claim live prices, room availability, or that a booking has been made.
      Never claim ratings or reviews; classification stars are unverified OpenStreetMap metadata.
    PROMPT

    TOOLS = [
      {
        type: "function",
        name: "get_weather",
        description: "Get live current weather for a destination.",
        parameters: {
          type: "object",
          properties: { destination: { type: "string" } },
          required: ["destination"],
          additionalProperties: false
        }
      },
      {
        type: "function",
        name: "find_hotels",
        description: "Find live nearby hotel listings from OpenStreetMap.",
        parameters: {
          type: "object",
          properties: { destination: { type: "string" } },
          required: ["destination"],
          additionalProperties: false
        }
      }
    ].freeze

    attr_reader :model

    def initialize(api_key:, base_url: DEFAULT_BASE_URL, model: "gpt-5.6", http: HttpJsonClient.new)
      raise ArgumentError, "PYDANTIC_AI_GATEWAY_API_KEY is required" if api_key.to_s.empty?

      @api_key = api_key
      selected_base_url = base_url.to_s.strip.empty? ? DEFAULT_BASE_URL : base_url
      normalized_base_url = selected_base_url.end_with?("/") ? selected_base_url : "#{selected_base_url}/"
      @endpoint = URI.join(normalized_base_url, "responses")
      @model = model
      @http = http
    end

    def plan(destination:)
      response = complete(
        instructions: SYSTEM_PROMPT,
        input: "Plan a hotel stay in #{destination}.",
        tools: TOOLS,
        tool_choice: "required",
        parallel_tool_calls: true
      )
      parsed = normalized_response(response)
      output = response.fetch("output")
      raise TypeError, "Gateway output must be an array" unless output.is_a?(Array)

      parsed[:tool_calls] = output.select { |item| item.is_a?(Hash) && item["type"] == "function_call" }.map do |call|
        {
          id: call.fetch("call_id"),
          name: call.fetch("name"),
          arguments: JSON.parse(call.fetch("arguments"))
        }
      end
      parsed
    rescue JSON::ParserError, KeyError, TypeError => error
      raise ExternalServiceError, "Gateway tool response was invalid: #{error.message}"
    end

    def recommend(destination:, weather:, hotels:, tool_calls:, previous_response_id:)
      tool_results = tool_calls.map do |call|
        result = call.fetch(:name) == "get_weather" ? weather : hotels
        { type: "function_call_output", call_id: call.fetch(:id), output: JSON.generate(result) }
      end
      response = complete(
        instructions: SYSTEM_PROMPT,
        previous_response_id: previous_response_id,
        input: tool_results
      )
      content = response.fetch("output").filter_map do |item|
        next unless item.is_a?(Hash) && item["type"] == "message"

        item.fetch("content").filter_map do |part|
          part["text"] if part.is_a?(Hash) && part["type"] == "output_text"
        end
      end.flatten.join("\n")
      raise ExternalServiceError, "Gateway response had no output text" if content.empty?

      normalized_response(response).merge(content: content)
    rescue KeyError, TypeError => error
      raise ExternalServiceError, "Gateway completion response was invalid: #{error.message}"
    end

    private

    attr_reader :api_key, :endpoint, :http

    def complete(payload)
      response = http.post_json(
        endpoint,
        { model: model }.merge(payload),
        headers: { "Authorization" => "Bearer #{api_key}" }
      )
      raise ExternalServiceError, "Gateway response was not an object" unless response.is_a?(Hash)

      response
    end

    def normalized_response(response)
      usage = response.fetch("usage")
      raise TypeError, "Gateway usage must be an object" unless usage.is_a?(Hash)

      {
        id: response.fetch("id"),
        model: response.fetch("model"),
        finish_reason: response.fetch("status", "unknown"),
        input_tokens: usage.fetch("input_tokens"),
        output_tokens: usage.fetch("output_tokens")
      }
    end
  end
end
