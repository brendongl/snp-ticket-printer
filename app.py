"""
SNP Printer Service
A minimal Flask service for printing to network ESC/POS thermal printers.
Designed to run on Unraid via Docker.
"""

import os
import socket
from datetime import datetime
from flask import Flask, request, jsonify
from escpos.printer import Network

app = Flask(__name__)

# Configuration from environment
PRINTER_TYPE = os.getenv('PRINTER_TYPE', 'network')
NETWORK_HOST = os.getenv('NETWORK_HOST', '192.168.1.100')
NETWORK_PORT = int(os.getenv('NETWORK_PORT', '9100'))
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'

# Multiple printer support (comma-separated hosts)
PRINTER_HOSTS = os.getenv('PRINTER_HOSTS', NETWORK_HOST).split(',')


def get_printer(host=None, port=None):
    """
    Get a network printer connection.
    Returns None if connection fails.
    """
    host = host or NETWORK_HOST
    port = port or NETWORK_PORT

    try:
        printer = Network(host, port=port, timeout=10)
        return printer
    except Exception as e:
        print(f"[ERROR] Failed to connect to printer at {host}:{port} - {e}")
        return None


def check_printer_reachable(host, port=9100, timeout=3):
    """
    Check if a printer is reachable on the network.
    Uses a simple TCP connection test.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        print(f"[ERROR] Socket check failed for {host}:{port} - {e}")
        return False


def discover_printers(subnet_prefix="192.168.1", port=9100, start=1, end=254):
    """
    Scan a subnet for printers listening on the specified port.
    This is a simple scan - may take time for large ranges.
    """
    found_printers = []

    for i in range(start, end + 1):
        host = f"{subnet_prefix}.{i}"
        if check_printer_reachable(host, port, timeout=0.5):
            found_printers.append({"host": host, "port": port})
            print(f"[FOUND] Printer at {host}:{port}")

    return found_printers


# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.route('/')
def index():
    """Root endpoint with service info."""
    return jsonify({
        "service": "SNP Printer Service",
        "version": "0.1.0",
        "status": "running",
        "endpoints": {
            "/health": "GET - Service health check",
            "/printer/status": "GET - Check configured printer status",
            "/printer/discover": "GET - Discover printers on network",
            "/print/test": "POST - Send test print",
            "/print/message": "POST - Print custom message"
        }
    })


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "printer_type": PRINTER_TYPE,
            "network_host": NETWORK_HOST,
            "network_port": NETWORK_PORT
        }
    })


@app.route('/printer/status')
def printer_status():
    """Check if configured printer(s) are reachable."""
    results = []

    for host in PRINTER_HOSTS:
        host = host.strip()
        reachable = check_printer_reachable(host, NETWORK_PORT)
        results.append({
            "host": host,
            "port": NETWORK_PORT,
            "reachable": reachable,
            "status": "online" if reachable else "offline"
        })

    all_online = all(r["reachable"] for r in results)

    return jsonify({
        "overall_status": "all_online" if all_online else "some_offline",
        "printers": results,
        "checked_at": datetime.now().isoformat()
    })


@app.route('/printer/discover')
def discover():
    """
    Discover printers on the local network.
    Optional query params:
    - subnet: Subnet prefix (default: 192.168.1)
    - port: Port to scan (default: 9100)
    - start: Start IP (default: 1)
    - end: End IP (default: 50, limited for speed)
    """
    subnet = request.args.get('subnet', '192.168.1')
    port = int(request.args.get('port', '9100'))
    start = int(request.args.get('start', '1'))
    end = min(int(request.args.get('end', '50')), 254)  # Cap at 254

    print(f"[DISCOVER] Scanning {subnet}.{start}-{end} on port {port}")

    found = discover_printers(subnet, port, start, end)

    return jsonify({
        "scan_range": f"{subnet}.{start}-{end}",
        "port": port,
        "found_count": len(found),
        "printers": found,
        "scanned_at": datetime.now().isoformat()
    })


@app.route('/print/test', methods=['POST'])
def print_test():
    """
    Send a test print to verify printer connection.
    Optional JSON body:
    - host: Specific printer host (uses default if not provided)
    """
    data = request.get_json() or {}
    host = data.get('host', NETWORK_HOST)

    # Check printer is reachable first
    if not check_printer_reachable(host, NETWORK_PORT):
        return jsonify({
            "success": False,
            "error": f"Printer at {host}:{NETWORK_PORT} is not reachable"
        }), 503

    try:
        printer = get_printer(host)
        if not printer:
            return jsonify({
                "success": False,
                "error": "Failed to connect to printer"
            }), 503

        # Print test ticket
        now = datetime.now()

        printer.set(align='center', bold=True, width=2, height=2)
        printer.text("SIP & PLAY\n")

        printer.set(align='center', bold=False, width=1, height=1)
        printer.text("=" * 32 + "\n")
        printer.text("PRINTER TEST\n")
        printer.text("=" * 32 + "\n\n")

        printer.set(align='left')
        printer.text(f"Time: {now.strftime('%H:%M:%S')}\n")
        printer.text(f"Date: {now.strftime('%Y-%m-%d')}\n")
        printer.text(f"Host: {host}\n")
        printer.text(f"Port: {NETWORK_PORT}\n\n")

        printer.set(align='center')
        printer.text("If you see this, it works!\n\n")

        printer.text("-" * 32 + "\n")
        printer.text("SNP Printer Service v0.1.0\n")

        printer.cut()
        printer.close()

        return jsonify({
            "success": True,
            "message": "Test print sent successfully",
            "printer": {"host": host, "port": NETWORK_PORT},
            "printed_at": now.isoformat()
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/print/message', methods=['POST'])
def print_message():
    """
    Print a custom message.
    JSON body:
    - message: Text to print (required)
    - title: Optional title/header
    - host: Specific printer host (uses default if not provided)
    """
    data = request.get_json()

    if not data or not data.get('message'):
        return jsonify({
            "success": False,
            "error": "Message is required"
        }), 400

    message = data.get('message')
    title = data.get('title', 'MESSAGE')
    host = data.get('host', NETWORK_HOST)

    # Check printer is reachable first
    if not check_printer_reachable(host, NETWORK_PORT):
        return jsonify({
            "success": False,
            "error": f"Printer at {host}:{NETWORK_PORT} is not reachable"
        }), 503

    try:
        printer = get_printer(host)
        if not printer:
            return jsonify({
                "success": False,
                "error": "Failed to connect to printer"
            }), 503

        now = datetime.now()

        # Print formatted message
        printer.set(align='center', bold=True, width=2, height=2)
        printer.text("SIP & PLAY\n")

        printer.set(align='center', bold=False, width=1, height=1)
        printer.text("=" * 32 + "\n")

        printer.set(align='center', bold=True)
        printer.text(f"{title}\n")

        printer.set(bold=False)
        printer.text("=" * 32 + "\n\n")

        printer.set(align='left')
        printer.text(f"Time: {now.strftime('%H:%M:%S')}\n")
        printer.text(f"Date: {now.strftime('%Y-%m-%d')}\n\n")

        printer.text("-" * 32 + "\n")
        printer.text(f"{message}\n")
        printer.text("-" * 32 + "\n\n")

        printer.cut()
        printer.close()

        return jsonify({
            "success": True,
            "message": "Message printed successfully",
            "printer": {"host": host, "port": NETWORK_PORT},
            "printed_at": now.isoformat()
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5000'))

    print(f"""
╔═══════════════════════════════════════════════════════╗
║           SNP PRINTER SERVICE v0.1.0                  ║
╠═══════════════════════════════════════════════════════╣
║  Server:  http://{host}:{port}
║  Printer: {NETWORK_HOST}:{NETWORK_PORT}
║  Debug:   {DEBUG}
╚═══════════════════════════════════════════════════════╝
    """)

    app.run(host=host, port=port, debug=DEBUG)
