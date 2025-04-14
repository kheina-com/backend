jq '.fullchain' -r /etc/certs/cert.json > fullchain.pem && \
jq '.privkey'   -r /etc/certs/cert.json > privkey.pem && \
gunicorn -w 2 -k uvicorn.workers.UvicornWorker --certfile fullchain.pem --keyfile privkey.pem -b 0.0.0.0:443 --timeout 1200 server:app
