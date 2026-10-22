# frozen_string_literal: true

require "json"
require "sinatra/base"

module RubyOtelDemo
  class App < Sinatra::Base
    configure do
      set :telemetry, nil
      set :show_exceptions, false
    end

    before do
      content_type :json
    end

    get "/" do
      json(
        name: "Ruby OpenTelemetry Collector demo",
        routes: ["/work", "/error", "/health"]
      )
    end

    get "/health" do
      json(status: "ok")
    end

    get "/work" do
      settings.telemetry.in_span("ruby-otel.work", attributes: { "demo.operation" => "work" }) do
        settings.telemetry.log(
          severity: "info",
          body: "ruby work completed",
          attributes: { "demo.operation" => "work" }
        )
        settings.telemetry.record_request(route: "/work", outcome: "success")
        json(operation: "ruby-otel.work", status: "completed")
      end
    end

    get "/error" do
      raise RuntimeError, "sensitive internal failure"
    rescue StandardError => error
      settings.telemetry.record_exception(error)
      settings.telemetry.log(
        severity: "error",
        body: "ruby work failed",
        attributes: { "demo.operation" => "error" }
      )
      settings.telemetry.record_request(route: "/error", outcome: "error")
      halt 500, json(error: "intentional demo failure", operation: "ruby-otel.error")
    end

    private

    def json(payload)
      JSON.generate(payload)
    end
  end
end
