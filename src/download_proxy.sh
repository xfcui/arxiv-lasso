#!/bin/bash

# Exit on error, undefined variables, and pipe failures
set -euo pipefail

# Load environment variables from .env
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# Proxy configuration
PROXY_PORT=${SSH_PROXY_PORT:-1080}
SSH_PID=""

# Function to check if a port is in use and kill the process
ensure_port_free() {
    local port=$1
    local pid
    pid=$(lsof -ti :"$port" || true)
    if [ -n "$pid" ]; then
        echo "Port $port is in use by PID $pid. Killing it..."
        kill -9 "$pid" || true
        sleep 1
    fi
}

# Function to start SSH tunnel
start_tunnel() {
    local host=$1
    ensure_port_free "$PROXY_PORT"
    echo "Starting SSH proxy tunnel to $host on port $PROXY_PORT..."
    
    # Use ControlPath for better management and faster connections
    ssh -D "$PROXY_PORT" -f -N "$host"
    
    # Wait a moment for the tunnel to establish
    local retries=5
    while [ $retries -gt 0 ]; do
        if lsof -ti :"$PROXY_PORT" > /dev/null; then
            SSH_PID=$(lsof -ti :"$PROXY_PORT")
            echo "SSH tunnel established (PID: $SSH_PID)"
            return 0
        fi
        sleep 1
        retries=$((retries - 1))
    done
    
    echo "Error: Failed to establish SSH tunnel to $host"
    return 1
}

# Function to stop SSH tunnel
stop_tunnel() {
    if [ -n "$SSH_PID" ]; then
        echo "Stopping SSH proxy tunnel (PID: $SSH_PID)..."
        kill "$SSH_PID" || true
        SSH_PID=""
    fi
}

# Ensure the tunnel is killed on exit
cleanup() {
    local exit_code=$?
    stop_tunnel
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

# Execute the provided command
if [ $# -gt 0 ]; then
    PROXY_USER_HOST=${SSH_PROXY_USER_HOST_SDU:-}
    if [ -z "$PROXY_USER_HOST" ]; then
        echo "Error: SSH_PROXY_USER_HOST_SDU not set in .env"
        exit 1
    fi
    start_tunnel "$PROXY_USER_HOST"
    echo "Executing provided command: $*"
    "$@"
else
    echo "No command provided. Running default download sequence..."
    
    echo "--- Starting non-proxy downloads ---"
    python src/download_rss.py || echo "Warning: download_rss.py failed"
    python src/download_springer.py || echo "Warning: download_springer.py failed"

    # Sequence 1: SDU Proxy
    echo "--- Starting SDU sequence ---"
    PROXY_USER_HOST=${SSH_PROXY_USER_HOST_SDU:-}
    if [ -n "$PROXY_USER_HOST" ]; then
        if start_tunnel "$PROXY_USER_HOST"; then
            python src/download_elsevier.py || echo "Warning: download_elsevier.py failed"
            stop_tunnel
        fi
    else
        echo "Warning: SSH_PROXY_USER_HOST_SDU not set, skipping SDU sequence"
    fi

    # Sequence 2: INT Proxy
    echo "--- Starting INT sequence ---"
    PROXY_USER_HOST=${SSH_PROXY_USER_HOST_INT:-}
    if [ -n "$PROXY_USER_HOST" ]; then
        if start_tunnel "$PROXY_USER_HOST"; then
            python src/download_ncbi.py || echo "Warning: download_ncbi.py failed"
            stop_tunnel
        fi
    else
        echo "Warning: SSH_PROXY_USER_HOST_INT not set, skipping INT sequence"
    fi
    echo "--- All sequences completed ---"
fi
