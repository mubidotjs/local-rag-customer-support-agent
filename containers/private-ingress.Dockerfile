FROM node:24-alpine@sha256:d8d97e479cdc64f052f81f129362168717c59783ade8dcc1abd22e1cca8dbd7c AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine@sha256:e2a8fca8aa356d66159aaf8a877a9c126773635adf210503eefaaeaffeed6ad7 AS runtime
COPY containers/nginx-ingress.conf /etc/nginx/nginx.conf
COPY --chmod=0555 containers/ingress-entrypoint.sh /usr/local/bin/ingress-entrypoint
COPY --from=build /build/dist /usr/share/nginx/html
USER 101:101
STOPSIGNAL SIGQUIT
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 CMD test -S /run/rag-ingress/http.sock && kill -0 "$(cat /tmp/nginx.pid)"
ENTRYPOINT ["/usr/local/bin/ingress-entrypoint"]
