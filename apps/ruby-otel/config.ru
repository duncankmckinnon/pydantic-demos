# frozen_string_literal: true

require_relative "lib/telemetry"
require_relative "app"

RubyOtelDemo::Telemetry.start!
RubyOtelDemo::App.set :telemetry, RubyOtelDemo::Telemetry

run RubyOtelDemo::App
