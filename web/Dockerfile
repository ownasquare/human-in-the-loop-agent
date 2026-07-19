# syntax=docker/dockerfile:1.7

FROM node:22-alpine
WORKDIR /app
ENV CYPRESS_INSTALL_BINARY=0

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY --chown=node:node . .
RUN npm run build

USER node
EXPOSE 5173

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:5173').then((r) => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))"]

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
