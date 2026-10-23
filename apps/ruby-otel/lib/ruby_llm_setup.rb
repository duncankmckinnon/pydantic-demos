# frozen_string_literal: true

require "ruby_llm"

module RubyOtelDemo
  module RubyLlmSetup
    DEFAULT_BASE_URL = "https://gateway-us.pydantic.dev/proxy/openai-responses"
    DEFAULT_MODEL = "gpt-5.6-terra"

    module_function

    def configure!(api_key:, base_url: DEFAULT_BASE_URL)
      raise ArgumentError, "PYDANTIC_AI_GATEWAY_API_KEY is required" if api_key.to_s.empty?

      RubyLLM.configure do |config|
        config.openai_api_key = api_key
        config.openai_api_base = base_url.to_s.strip.empty? ? DEFAULT_BASE_URL : base_url
      end
    end
  end
end
