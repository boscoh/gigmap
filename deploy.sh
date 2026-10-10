#!/bin/bash
# Deploy the gig map to the tspots instance on DigitalOcean.
# The record of the setup is in docs/deploy.md.
#
# Usage:
#   ./deploy.sh           deploy, IP-only on http://209.38.23.184:8080
#   ./deploy.sh --ssl     deploy with the domain and SSL. Add the A records first
#   ./deploy.sh update    send a change and restart the app
#   ./deploy.sh status    show the supervisor state
#   ./deploy.sh logs      show the app log
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# deployvm reads {instance}.instance.json from the current directory.
INSTANCE_DIR="/Users/boscoh/p/nimago"
INSTANCE="tspots"
APP_NAME="gigmap"
PORT=8000
OUTGOING_PORT=8080
DOMAIN="gig.tangiblespots.com"
EMAIL="apposite@gmail.com"
COMMAND="TZ=Australia/Melbourne uv run --no-sync gigmap serve --host 0.0.0.0 --port 8000"

if ! command -v deployvm >/dev/null 2>&1; then
    echo "deployvm not found in PATH" >&2
    exit 1
fi

if [ ! -f "$INSTANCE_DIR/$INSTANCE.instance.json" ]; then
    echo "missing $INSTANCE_DIR/$INSTANCE.instance.json" >&2
    exit 1
fi

cd "$INSTANCE_DIR"

case "${1:-deploy}" in
    deploy)
        deployvm uv deploy "$INSTANCE" "$APP_DIR" "$COMMAND" \
            --app-name "$APP_NAME" \
            --port "$PORT" \
            --outgoing-port "$OUTGOING_PORT"
        ;;
    --ssl)
        deployvm uv deploy "$INSTANCE" "$APP_DIR" "$COMMAND" \
            --app-name "$APP_NAME" \
            --port "$PORT" \
            --domain "$DOMAIN" \
            --email "$EMAIL"
        ;;
    update)
        deployvm uv sync "$INSTANCE" "$APP_DIR" "$COMMAND" --app-name "$APP_NAME"
        ;;
    status)
        deployvm uv status "$INSTANCE"
        ;;
    logs)
        deployvm uv logs "$INSTANCE" --app-name "$APP_NAME"
        ;;
    *)
        echo "usage: $0 [deploy|--ssl|update|status|logs]" >&2
        exit 2
        ;;
esac
