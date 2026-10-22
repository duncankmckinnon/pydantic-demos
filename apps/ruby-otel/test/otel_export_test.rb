# frozen_string_literal: true

require "socket"
require "timeout"

require_relative "test_helper"
require_relative "../lib/telemetry"

class LoopbackOtlpReceiver
  attr_reader :port, :requests

  def initialize
    @server = TCPServer.new("127.0.0.1", 0)
    @port = @server.local_address.ip_port
    @requests = []
    @mutex = Mutex.new
    @thread = Thread.new { accept_requests }
  end

  def wait_for_paths(paths, timeout: 5)
    Timeout.timeout(timeout) do
      sleep 0.01 until paths.all? { |path| requests.any? { |request| request.fetch(:path) == path } }
    end
  end

  def close
    @server.close
    @thread.join(1)
  end

  private

  def accept_requests
    loop do
      socket = @server.accept
      request_line = socket.gets
      next socket.close unless request_line

      method, path, = request_line.split
      headers = {}
      while (line = socket.gets)
        break if line == "\r\n"

        name, value = line.split(":", 2)
        headers[name.downcase] = value.strip
      end
      socket.read(headers.fetch("content-length", "0").to_i)
      @mutex.synchronize { @requests << { method: method, path: path, headers: headers } }
      socket.write("HTTP/1.1 200 OK\r\nContent-Type: application/x-protobuf\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
      socket.close
    end
  rescue IOError, Errno::EBADF
    nil
  end
end

class OtlpExportTest < Minitest::Test
  include Rack::Test::Methods

  SIGNAL_PATHS = ["/v1/traces", "/v1/metrics", "/v1/logs"].freeze
  ENVIRONMENT_KEYS = %w[
    OTEL_EXPORTER_OTLP_PROTOCOL
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
    OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
    OTEL_EXPORTER_OTLP_LOGS_ENDPOINT
    OTEL_METRIC_EXPORT_INTERVAL
  ].freeze

  def app
    RubyOtelDemo::App
  end

  def setup
    @receiver = LoopbackOtlpReceiver.new
    @previous_environment = ENVIRONMENT_KEYS.to_h { |name| [name, ENV[name]] }
    ENV["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    ENV["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = endpoint_for("traces")
    ENV["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = endpoint_for("metrics")
    ENV["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = endpoint_for("logs")
    ENV["OTEL_METRIC_EXPORT_INTERVAL"] = "60000"

    RubyOtelDemo::Telemetry.start!(instrument: false)
    app.set :telemetry, RubyOtelDemo::Telemetry
    header "Host", "localhost"
  end

  def teardown
    RubyOtelDemo::Telemetry.shutdown!
    @receiver.close
    @previous_environment.each { |name, value| value.nil? ? ENV.delete(name) : ENV[name] = value }
  end

  def test_work_exports_traces_metrics_and_logs_to_the_configured_loopback_receiver
    get "/work"
    assert_equal 200, last_response.status

    RubyOtelDemo::Telemetry.shutdown!
    @receiver.wait_for_paths(SIGNAL_PATHS)

    assert_equal SIGNAL_PATHS.sort, @receiver.requests.map { |request| request.fetch(:path) }.uniq.sort
    assert @receiver.requests.all? { |request| request.fetch(:method) == "POST" }
    assert @receiver.requests.all? { |request| request.fetch(:headers)["authorization"].nil? }
  end

  private

  def endpoint_for(signal)
    "http://127.0.0.1:#{@receiver.port}/v1/#{signal}"
  end
end
