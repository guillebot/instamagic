# Pin to multi-arch manifest-list digests (index), not per-arch manifests.
FROM node:22-alpine@sha256:ce8d97f7cfc7c5386421eb932df20a29df99b1a7f469f6157d5c9956702ffadc AS build
ARG APP_VERSION=dev
ENV VITE_APP_VERSION=${APP_VERSION}
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
