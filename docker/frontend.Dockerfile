# Build the UI, then serve the static bundle from nginx.
FROM node:20-alpine AS build

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --omit=optional

COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
