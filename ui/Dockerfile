FROM nginx:1.28-alpine
USER root
RUN apk update \
    && apk upgrade --no-cache \
    && apk del --no-cache \
        nginx-module-acme \
        nginx-module-geoip \
        nginx-module-image-filter \
        nginx-module-njs \
        nginx-module-xslt \
    && apk add --no-cache "nginx>=1.28.3-r7"

COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/index.html

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p \
        /tmp/client_temp \
        /tmp/proxy_temp \
        /tmp/fastcgi_temp \
        /tmp/uwsgi_temp \
        /tmp/scgi_temp \
    && chmod 1777 \
        /tmp/client_temp \
        /tmp/proxy_temp \
        /tmp/fastcgi_temp \
        /tmp/uwsgi_temp \
        /tmp/scgi_temp

USER 101:101

EXPOSE 8080

STOPSIGNAL SIGQUIT

CMD ["nginx", "-g", "daemon off;"]