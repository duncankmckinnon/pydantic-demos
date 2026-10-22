FROM ruby:3.3.12-slim-bookworm AS builder

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY apps/ruby-otel/Gemfile apps/ruby-otel/Gemfile.lock ./
RUN bundle config set deployment true \
    && bundle config set path /usr/local/bundle \
    && bundle install

FROM ruby:3.3.12-slim-bookworm

RUN groupadd --system ruby-otel \
    && useradd --system --gid ruby-otel --create-home ruby-otel

WORKDIR /app
COPY --from=builder /usr/local/bundle /usr/local/bundle
COPY apps/ruby-otel/app.rb apps/ruby-otel/config.ru ./
COPY apps/ruby-otel/lib ./lib
RUN chown -R ruby-otel:ruby-otel /app

USER ruby-otel
EXPOSE 9292

CMD ["bundle", "exec", "rackup", "--server", "puma", "--host", "0.0.0.0", "--port", "9292"]
