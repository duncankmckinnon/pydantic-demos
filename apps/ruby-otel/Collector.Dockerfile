FROM otel/opentelemetry-collector-contrib:0.137.0 AS collector

FROM alpine:3.22.1

RUN apk add --no-cache ca-certificates \
    && addgroup -S otel \
    && adduser -S -G otel otel

COPY --from=collector /otelcol-contrib /otelcol-contrib
COPY apps/ruby-otel/bin/start-collector /usr/local/bin/start-collector
RUN chmod 0755 /usr/local/bin/start-collector

USER otel
EXPOSE 4318 13133

ENTRYPOINT ["/usr/local/bin/start-collector"]
CMD ["--config=/etc/otelcol-contrib/config.yaml"]
