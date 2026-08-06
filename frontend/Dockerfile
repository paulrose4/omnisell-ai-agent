ARG NODE_IMAGE=public.ecr.aws/docker/library/node:22.23.1-alpine3.23@sha256:8516dce0483394d5708d4b2ee6cacb79fb1d617ea4e2787c2120bcca92ce372e

FROM ${NODE_IMAGE} AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ARG NEXT_PUBLIC_ENVIRONMENT=production
ARG NEXT_PUBLIC_MOCK_DRILL_ENABLED=false
ARG NEXT_PUBLIC_RELEASE_PROFILE=full_development
ARG NEXT_PUBLIC_SELLER_CENTRAL_URL=https://sellercentral.amazon.com
ARG NEXT_PUBLIC_REFRESH_INTERVAL_MS=5000
ENV NEXT_PUBLIC_ENVIRONMENT=${NEXT_PUBLIC_ENVIRONMENT} \
    NEXT_PUBLIC_MOCK_DRILL_ENABLED=${NEXT_PUBLIC_MOCK_DRILL_ENABLED} \
    NEXT_PUBLIC_RELEASE_PROFILE=${NEXT_PUBLIC_RELEASE_PROFILE} \
    NEXT_PUBLIC_SELLER_CENTRAL_URL=${NEXT_PUBLIC_SELLER_CENTRAL_URL} \
    NEXT_PUBLIC_REFRESH_INTERVAL_MS=${NEXT_PUBLIC_REFRESH_INTERVAL_MS}
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
COPY --chown=node:node --from=build /app/.next/standalone ./
COPY --chown=node:node --from=build /app/.next/static ./.next/static
COPY --chown=node:node --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
