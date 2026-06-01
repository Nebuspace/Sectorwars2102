# nginx-gateway has no dev/prod distinction at the image level — the
# Vite-dev-mode workarounds (stripped static-asset location, dotfile-deny
# rules) live inside nginx.conf and are conditionally enabled by other
# means. Both targets resolve to the same image so dev compose can use
# `target: dev` and the publish workflow can use `target: production`
# uniformly across services.

FROM nginx:stable-alpine AS base

COPY nginx.conf /etc/nginx/nginx.conf

RUN mkdir -p /etc/nginx/ssl /etc/nginx/conf.d/regions /var/log/nginx \
    && chown -R nginx:nginx /var/log/nginx

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]

# Both targets are aliases for `base` — kept explicit so the build matrix can
# pass `target: dev|production` without conditional logic.
FROM base AS dev
FROM base AS production
