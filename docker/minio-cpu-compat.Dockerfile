FROM minio/minio:RELEASE.2024-01-16T16-07-38Z AS minio

FROM alpine:3.19

COPY --from=minio /usr/bin/minio /usr/bin/minio

RUN addgroup -S minio \
    && adduser -S -G minio minio \
    && mkdir -p /data \
    && chown -R minio:minio /data

EXPOSE 9000 9001

USER minio
ENTRYPOINT ["/usr/bin/minio"]
CMD ["server", "/data"]
