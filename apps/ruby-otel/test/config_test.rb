# frozen_string_literal: true

require "open3"
require "yaml"

require_relative "test_helper"

class ConfigTest < Minitest::Test
  REPO_ROOT = File.expand_path("../../..", __dir__)
  APP_ROOT = File.join(REPO_ROOT, "apps/ruby-otel")
  COLLECTOR_CONFIG = File.join(REPO_ROOT, "infra/otel-collector/ruby-otel.yaml")

  def test_ruby_service_uses_only_the_collector_otlp_endpoint
    service = compose.fetch("services").fetch("ruby-otel")

    assert_equal ["ruby-otel", "all"], service.fetch("profiles")
    assert_equal ["${RUBY_OTEL_PORT:-8006}:9292"], service.fetch("ports")
    assert_equal "pydantic-demos-ruby-otel:local", service["image"]
    assert_equal "http://otel-collector-ruby-otel:4318", service.fetch("environment").fetch("OTEL_EXPORTER_OTLP_ENDPOINT")
    assert_equal "apps/ruby-otel/agent.env", service["env_file"]
    refute service.fetch("environment").key?("LOGFIRE_TOKEN")
    refute service.fetch("environment").key?("LOGFIRE_BASE_URL")
  end

  def test_collector_alone_loads_the_demo_env_and_uses_pinned_images
    service = compose.fetch("services").fetch("otel-collector-ruby-otel")
    dockerfile = File.read(File.join(APP_ROOT, "Collector.Dockerfile"))

    assert_equal ["ruby-otel", "all"], service.fetch("profiles")
    assert_equal "apps/ruby-otel/.env", service.fetch("env_file")
    assert_equal "apps/ruby-otel/Collector.Dockerfile", service.fetch("build").fetch("dockerfile")
    assert_match(/^FROM otel\/opentelemetry-collector-contrib:\d+\.\d+\.\d+ AS collector$/, dockerfile)
    assert_match(/^FROM alpine:\d+\.\d+\.\d+$/, dockerfile)
    refute_match(/:latest\b/, dockerfile)
  end

  def test_collector_has_three_active_otlp_pipelines_and_health_check
    config = YAML.safe_load_file(COLLECTOR_CONFIG)
    receiver = config.fetch("receivers").fetch("otlp").fetch("protocols").fetch("http")

    assert_equal "0.0.0.0:4318", receiver.fetch("endpoint")
    assert config.fetch("processors").key?("memory_limiter")
    assert config.fetch("processors").key?("batch")
    assert config.fetch("extensions").key?("health_check")
    assert_includes config.fetch("service").fetch("extensions"), "health_check"

    %w[traces metrics logs].each do |signal|
      pipeline = config.fetch("service").fetch("pipelines").fetch(signal)
      assert_equal ["otlp"], pipeline.fetch("receivers")
      assert_equal ["memory_limiter", "batch"], pipeline.fetch("processors")
      assert_equal ["otlphttp/logfire"], pipeline.fetch("exporters")
    end
  end

  def test_collector_exports_to_logfire_using_only_its_environment
    exporter = YAML.safe_load_file(COLLECTOR_CONFIG).fetch("exporters").fetch("otlphttp/logfire")

    assert_equal "${env:LOGFIRE_BASE_URL}", exporter.fetch("endpoint")
    assert_equal "${env:LOGFIRE_TOKEN}", exporter.fetch("headers").fetch("Authorization")
  end

  def test_collector_entrypoint_rejects_blank_credentials
    script = File.join(APP_ROOT, "bin/start-collector")

    [
      { "LOGFIRE_TOKEN" => "", "LOGFIRE_BASE_URL" => "https://logfire-us.pydantic.dev" },
      { "LOGFIRE_TOKEN" => "dummy", "LOGFIRE_BASE_URL" => "" }
    ].each do |environment|
      _stdout, stderr, status = Open3.capture3(environment, "sh", script, "validate")
      refute status.success?
      assert_includes stderr, "LOGFIRE_TOKEN and LOGFIRE_BASE_URL must both be set"
    end
  end

  def test_environment_examples_define_only_the_expected_values
    app_environment = dotenv(File.join(APP_ROOT, ".env.example"))
    agent_example = File.join(APP_ROOT, "agent.env.example")
    agent_environment = File.exist?(agent_example) ? dotenv(agent_example) : {}
    root_environment = dotenv(File.join(REPO_ROOT, ".env.example"))

    assert_equal(
      {
        "LOGFIRE_TOKEN" => "",
        "LOGFIRE_BASE_URL" => "https://logfire-us.pydantic.dev"
      },
      app_environment
    )
    assert_equal(
      {
        "PYDANTIC_AI_GATEWAY_API_KEY" => "",
        "PYDANTIC_AI_GATEWAY_BASE_URL" => "https://gateway-us.pydantic.dev/proxy/chat/",
        "PYDANTIC_AI_GATEWAY_MODEL" => "gpt-5.6",
        "NOMINATIM_ENDPOINT" => "https://nominatim.openstreetmap.org/search"
      },
      agent_environment
    )
    assert_equal "8006", root_environment.fetch("RUBY_OTEL_PORT")
  end

  private

  def compose
    @compose ||= YAML.safe_load_file(File.join(REPO_ROOT, "docker-compose.yml"))
  end

  def dotenv(path)
    File.readlines(path, chomp: true).filter_map do |line|
      next if line.empty? || line.start_with?("#")

      line.split("=", 2)
    end.to_h
  end
end
