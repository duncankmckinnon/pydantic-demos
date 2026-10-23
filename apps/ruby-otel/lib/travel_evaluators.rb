# frozen_string_literal: true

module RubyOtelDemo
  module TravelEvaluators
    Result = Struct.new(:name, :passed, :explanation, keyword_init: true)

    NUMBERED_RECOMMENDATION = /^\s*\d+[.)]\s+(?:\*\*)?(.+?)(?:\*\*)?\s+(?:[\u2014\u2013-]\s+|:\s+)/
    CURRENCY_CLAIM = /[$€£¥]\s?\d|\b\d+(?:\.\d+)?\s?(?:USD|EUR|GBP|JPY)\b/i
    AVAILABILITY_CLAIM = /\b(?:rooms?|suites?)\s+(?:are|remain|look)\s+available\b|\b(?:has|offers?)\s+availability\b|\bavailability\s+(?:is|looks|remains)\b/i
    BOOKING_CLAIM = /\b(?:i|we)(?:'ve| have)?\s+(?:booked|reserved)\b|\b(?:booking|reservation)\s+(?:is\s+)?confirmed\b|\byour\s+(?:room|stay|booking|reservation)\s+(?:is\s+)?(?:booked|reserved|confirmed)\b/i

    module_function

    def evaluate(response:, weather:, hotels:)
      hotel_names = hotels.filter_map { |hotel| hotel[:name] || hotel["name"] }
      recommendations = numbered_recommendations(response)

      [
        recommendation_count(response, hotel_names, recommendations),
        hotel_grounding(response, hotel_names, recommendations),
        weather_grounding(response, weather),
        no_booking_claims(response)
      ]
    end

    def recommendation_count(response, hotel_names, recommendations)
      count = if recommendations.empty?
                mentioned_hotels(response, hotel_names).length
              else
                recommendations.length
              end
      passed = count.between?(1, 3)
      Result.new(
        name: "recommendation_count",
        passed: passed,
        explanation: passed ? "Response recommends #{count} hotel(s)." : "Response recommends #{count} hotel(s); expected 1 to 3."
      )
    end

    def hotel_grounding(response, hotel_names, recommendations)
      unknown = recommendations.reject do |candidate|
        hotel_names.any? { |name| normalize(candidate) == normalize(name) }
      end
      matched = mentioned_hotels(response, hotel_names)
      passed = unknown.empty? && matched.any?
      explanation = if unknown.any?
                      "Recommendations not returned by the hotel tool: #{unknown.join(', ')}."
                    elsif matched.empty?
                      "No recommended hotel could be matched to the hotel tool results."
                    else
                      "All recommended hotels were returned by the hotel tool."
                    end
      Result.new(name: "hotel_grounding", passed: passed, explanation: explanation)
    end

    def weather_grounding(response, weather)
      condition = (weather[:weather] || weather["weather"]).to_s.strip
      temperature = weather[:temperature_c] || weather["temperature_c"]
      condition_mentioned = !condition.empty? && response.downcase.include?(condition.downcase)
      temperature_mentioned = temperature && temperature_mentioned?(response, Float(temperature))
      passed = condition_mentioned || temperature_mentioned
      Result.new(
        name: "weather_grounding",
        passed: passed,
        explanation: passed ? "Response references the fetched weather." : "Response does not reference the fetched condition or temperature."
      )
    rescue ArgumentError, TypeError
      Result.new(
        name: "weather_grounding",
        passed: false,
        explanation: "Fetched weather did not contain a usable condition or temperature."
      )
    end

    def no_booking_claims(response)
      unsupported_claim = [CURRENCY_CLAIM, AVAILABILITY_CLAIM, BOOKING_CLAIM].any? { |pattern| response.match?(pattern) }
      Result.new(
        name: "no_booking_claims",
        passed: !unsupported_claim,
        explanation: unsupported_claim ? "Response makes an unsupported price or availability claim." : "Response makes no unsupported price, availability, or booking claims."
      )
    end

    def numbered_recommendations(response)
      response.each_line.filter_map do |line|
        match = line.match(NUMBERED_RECOMMENDATION)
        normalize_display_name(match[1]) if match
      end
    end

    def mentioned_hotels(response, hotel_names)
      normalized_response = normalize(response)
      hotel_names.select { |name| normalized_response.include?(normalize(name)) }.uniq
    end

    def temperature_mentioned?(response, temperature)
      exact = format("%.1f", temperature)
      rounded = temperature.round.to_s
      response.match?(/(?:\b#{Regexp.escape(exact)}|\b#{Regexp.escape(rounded)})\s*(?:°\s*C|degrees?\s+C(?:elsius)?)/i)
    end

    def normalize(value)
      value.to_s.downcase.gsub(/[^[:alnum:]]+/, " ").strip
    end

    def normalize_display_name(value)
      value.to_s.gsub(/\A\*+|\*+\z/, "").strip
    end
  end
end
