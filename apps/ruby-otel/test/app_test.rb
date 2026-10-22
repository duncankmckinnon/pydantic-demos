# frozen_string_literal: true

require_relative "test_helper"
require_relative "../lib/travel_clients"

class FailingTravelAgent
  def call(destination:)
    raise RubyOtelDemo::ExternalServiceError, "upstream leaked details for #{destination}"
  end
end

class AppTest < RubyOtelDemoTest
  def test_root_lists_demo_routes
    get "/"

    assert_equal 200, last_response.status
    assert_includes last_response.content_type, "application/json"
    assert_equal ["/travel", "/work", "/error", "/health"], parsed_body.fetch("routes")
  end

  def test_travel_returns_live_agent_result_for_destination
    post "/travel", JSON.generate(destination: "Kyoto, Japan"), "CONTENT_TYPE" => "application/json"

    assert_equal 200, last_response.status
    assert_equal "Kyoto, Japan", parsed_body.fetch("destination")
    assert_equal "Pack a light jacket and stay near the city center.", parsed_body.fetch("recommendation")
    assert_equal ["Kyoto, Japan"], @travel_agent.destinations
    assert_equal [{ method: :record_request, route: "/travel", outcome: "success" }], calls_for(:record_request)
  end

  def test_travel_rejects_a_missing_destination_without_running_agent
    post "/travel", JSON.generate(destination: "  "), "CONTENT_TYPE" => "application/json"

    assert_equal 400, last_response.status
    assert_equal({ "error" => "destination is required" }, parsed_body)
    assert_empty @travel_agent.destinations
  end

  def test_travel_rejects_malformed_json
    post "/travel", "not-json", "CONTENT_TYPE" => "application/json"

    assert_equal 400, last_response.status
    assert_equal({ "error" => "invalid JSON request" }, parsed_body)
  end

  def test_travel_returns_sanitized_bad_gateway_and_records_failure
    app.set :travel_agent, FailingTravelAgent.new

    post "/travel", JSON.generate(destination: "Kyoto, Japan"), "CONTENT_TYPE" => "application/json"

    assert_equal 502, last_response.status
    assert_equal({ "error" => "live travel data is temporarily unavailable" }, parsed_body)
    refute_includes last_response.body, "upstream leaked details"
    assert_equal 1, calls_for(:record_exception).length
    assert_equal "travel agent failed", calls_for(:log).first.fetch(:body)
    assert_equal [{ method: :record_request, route: "/travel", outcome: "error" }], calls_for(:record_request)
  end

  def test_health_is_ready_without_emitting_demo_telemetry
    get "/health"

    assert_equal 200, last_response.status
    assert_equal({ "status" => "ok" }, parsed_body)
    assert_empty @telemetry.calls
  end

  def test_work_emits_child_span_log_and_success_metric
    get "/work"

    assert_equal 200, last_response.status
    assert_equal "ruby-otel.work", parsed_body.fetch("operation")
    assert_equal 1, calls_for(:in_span).length
    assert_equal "ruby-otel.work", calls_for(:in_span).first.fetch(:name)
    assert_equal 1, calls_for(:log).length
    assert_equal "ruby work completed", calls_for(:log).first.fetch(:body)
    assert_equal [{ method: :record_request, route: "/work", outcome: "success" }], calls_for(:record_request)
  end

  def test_error_records_exception_and_returns_sanitized_payload
    get "/error"

    assert_equal 500, last_response.status
    assert_equal(
      { "error" => "intentional demo failure", "operation" => "ruby-otel.error" },
      parsed_body
    )
    refute_includes last_response.body, "sensitive internal failure"
    refute_includes last_response.body, "app.rb:"
    assert_equal 1, calls_for(:record_exception).length
    assert_instance_of RuntimeError, calls_for(:record_exception).first.fetch(:exception)
    assert_equal "error", calls_for(:log).first.fetch(:severity)
    assert_equal [{ method: :record_request, route: "/error", outcome: "error" }], calls_for(:record_request)
  end

  def test_metrics_use_only_bounded_route_and_outcome_values
    get "/work"
    get "/error"

    assert_equal(
      [
        { method: :record_request, route: "/work", outcome: "success" },
        { method: :record_request, route: "/error", outcome: "error" }
      ],
      calls_for(:record_request)
    )
  end

  private

  def calls_for(method)
    @telemetry.calls.select { |call| call.fetch(:method) == method }
  end
end
