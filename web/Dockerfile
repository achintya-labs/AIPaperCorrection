FROM docker.io/library/node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV ASTRO_TELEMETRY_DISABLED=1
RUN npm run build

FROM docker.io/library/node:22-slim
WORKDIR /app
ENV HOST=0.0.0.0 PORT=4321 NODE_ENV=production
COPY package*.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
USER node
EXPOSE 4321
CMD ["node", "./dist/server/entry.mjs"]
