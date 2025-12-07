"""
SNP Printer Service v0.2.0
Flask service for printing to network ESC/POS thermal printers.
Designed for Unraid deployment with webhook/API support.
"""

import os
import socket
import hashlib
import time
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, abort
from escpos.printer import Network

app = Flask(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

PRINTER_TYPE = os.getenv('PRINTER_TYPE', 'network')
NETWORK_HOST = os.getenv('NETWORK_HOST', '192.168.50.103')
NETWORK_PORT = int(os.getenv('NETWORK_PORT', '9100'))
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'

# API Security
API_KEYS = {
    'snp-site': os.getenv('API_KEY_SNP_SITE', 'dev-key-snp-site'),
    'admin': os.getenv('API_KEY_ADMIN', 'dev-key-admin'),
}
REQUIRE_AUTH = os.getenv('REQUIRE_AUTH', 'false').lower() == 'true'

# Rate limiting (simple in-memory)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 10  # requests per window
rate_limit_store = {}

# Multiple printer support
PRINTERS = {
    'default': {'host': NETWORK_HOST, 'port': NETWORK_PORT},
    # Add more printers here via env vars
}
if os.getenv('PRINTER_2_HOST'):
    PRINTERS['printer2'] = {
        'host': os.getenv('PRINTER_2_HOST'),
        'port': int(os.getenv('PRINTER_2_PORT', '9100'))
    }


# =============================================================================
# HELPERS
# =============================================================================

def get_printer(printer_id='default'):
    """Get a network printer connection."""
    config = PRINTERS.get(printer_id, PRINTERS['default'])
    try:
        printer = Network(config['host'], port=config['port'], timeout=10)
        return printer
    except Exception as e:
        print(f"[ERROR] Failed to connect to printer {printer_id}: {e}")
        return None


def check_printer_reachable(host, port=9100, timeout=3):
    """Check if a printer is reachable on the network."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def check_rate_limit(key):
    """Simple rate limiting. Returns True if allowed, False if rate limited."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Clean old entries
    if key in rate_limit_store:
        rate_limit_store[key] = [t for t in rate_limit_store[key] if t > window_start]
    else:
        rate_limit_store[key] = []

    # Check limit
    if len(rate_limit_store[key]) >= RATE_LIMIT_MAX:
        return False

    # Record this request
    rate_limit_store[key].append(now)
    return True


# =============================================================================
# AUTHENTICATION DECORATOR
# =============================================================================

def require_api_key(f):
    """Decorator to require API key authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not REQUIRE_AUTH:
            return f(*args, **kwargs)

        api_key = request.headers.get('X-API-Key')
        if not api_key:
            api_key = request.args.get('api_key')

        if not api_key or api_key not in API_KEYS.values():
            return jsonify({'error': 'Unauthorized - Invalid or missing API key'}), 401

        # Rate limiting by API key
        if not check_rate_limit(api_key):
            return jsonify({'error': 'Rate limit exceeded. Try again later.'}), 429

        return f(*args, **kwargs)
    return decorated


# =============================================================================
# PRINT TEMPLATES
# =============================================================================

def print_header(printer, title="SIP & PLAY"):
    """Print standard header."""
    printer.set(align='center', bold=True, width=2, height=2)
    printer.text(f"{title}\n")
    printer.set(align='center', bold=False, width=1, height=1)
    printer.text("=" * 32 + "\n")


def print_footer(printer, cut=True):
    """Print standard footer."""
    printer.set(align='center', width=1, height=1)
    printer.text("-" * 32 + "\n")
    printer.text(f"{datetime.now().strftime('%H:%M %d/%m/%Y')}\n")
    printer.text("-" * 32 + "\n\n")
    if cut:
        printer.cut()


def print_message(printer, title, message, subtitle=None):
    """Print a simple message."""
    print_header(printer)

    printer.set(align='center', bold=True)
    printer.text(f"{title}\n")

    if subtitle:
        printer.set(bold=False)
        printer.text(f"{subtitle}\n")

    printer.text("=" * 32 + "\n\n")

    printer.set(align='left', bold=False)
    # Handle multi-line messages
    for line in message.split('\n'):
        printer.text(f"{line}\n")
    printer.text("\n")

    print_footer(printer)


def print_reminder(printer, reminder_type, staff_name, message, action_url=None):
    """Print a staff reminder with optional QR code."""
    print_header(printer)

    printer.set(align='center', bold=True)
    printer.text(f"REMINDER\n")
    printer.set(bold=False)
    printer.text(f"{reminder_type}\n")
    printer.text("=" * 32 + "\n\n")

    printer.set(align='left')
    if staff_name:
        printer.text(f"Staff: {staff_name}\n")
    printer.text(f"Time: {datetime.now().strftime('%H:%M')}\n\n")

    printer.text("-" * 32 + "\n")
    for line in message.split('\n'):
        printer.text(f"{line}\n")
    printer.text("-" * 32 + "\n\n")

    if action_url:
        printer.set(align='center')
        printer.text("Scan to take action:\n")
        printer.qr(action_url, size=6)
        printer.text("\n")

    print_footer(printer)


def print_task(printer, task_title, task_description, due_time=None, priority=None, assignee=None):
    """Print a task notification."""
    print_header(printer)

    printer.set(align='center', bold=True)
    printer.text("TASK\n")

    if priority:
        priority_display = {"high": "!!!", "medium": "!!", "low": "!"}.get(priority.lower(), "")
        printer.text(f"{priority_display} {priority.upper()} {priority_display}\n")

    printer.set(bold=False)
    printer.text("=" * 32 + "\n\n")

    printer.set(align='left', bold=True)
    printer.text(f"{task_title}\n")
    printer.set(bold=False)
    printer.text("-" * 32 + "\n")

    if task_description:
        for line in task_description.split('\n'):
            printer.text(f"{line}\n")
        printer.text("\n")

    if assignee:
        printer.text(f"Assigned: {assignee}\n")
    if due_time:
        printer.text(f"Due: {due_time}\n")

    printer.text("\n")
    print_footer(printer)


def print_order(printer, order_items, table_number=None, notes=None):
    """Print an order ticket (for future cafe integration)."""
    print_header(printer, "ORDER")

    if table_number:
        printer.set(align='center', bold=True, width=2, height=2)
        printer.text(f"TABLE {table_number}\n")
        printer.set(width=1, height=1, bold=False)

    printer.text("=" * 32 + "\n\n")

    printer.set(align='left')
    for item in order_items:
        qty = item.get('qty', 1)
        name = item.get('name', 'Item')
        printer.set(bold=True)
        printer.text(f"{qty}x {name}\n")
        if item.get('notes'):
            printer.set(bold=False)
            printer.text(f"   -> {item['notes']}\n")

    if notes:
        printer.text("\n" + "-" * 32 + "\n")
        printer.set(bold=True)
        printer.text("NOTES:\n")
        printer.set(bold=False)
        printer.text(f"{notes}\n")

    printer.text("\n")
    print_footer(printer)


# =============================================================================
# API ENDPOINTS - Info & Health
# =============================================================================

@app.route('/')
def index():
    """Root endpoint with service info."""
    return jsonify({
        "service": "SNP Printer Service",
        "version": "0.2.0",
        "status": "running",
        "auth_required": REQUIRE_AUTH,
        "endpoints": {
            "GET /health": "Service health check",
            "GET /printer/status": "Check printer status",
            "GET /printer/discover": "Discover printers on network",
            "POST /print/test": "Send test print",
            "POST /print/message": "Print custom message",
            "POST /print/reminder": "Print staff reminder",
            "POST /print/task": "Print task notification",
            "POST /webhook/message": "Webhook for messages",
            "POST /webhook/reminder": "Webhook for reminders",
        }
    })


@app.route('/health')
def health():
    """Health check endpoint."""
    printer_status = check_printer_reachable(NETWORK_HOST, NETWORK_PORT)
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "printer": {
            "host": NETWORK_HOST,
            "port": NETWORK_PORT,
            "reachable": printer_status
        }
    })


@app.route('/printer/status')
def printer_status():
    """Check all configured printers."""
    results = []
    for name, config in PRINTERS.items():
        reachable = check_printer_reachable(config['host'], config['port'])
        results.append({
            "name": name,
            "host": config['host'],
            "port": config['port'],
            "status": "online" if reachable else "offline"
        })

    return jsonify({
        "printers": results,
        "checked_at": datetime.now().isoformat()
    })


@app.route('/printer/discover')
def discover():
    """Discover printers on the local network."""
    subnet = request.args.get('subnet', '192.168.50')
    port = int(request.args.get('port', '9100'))
    start = int(request.args.get('start', '1'))
    end = min(int(request.args.get('end', '50')), 254)

    found = []
    for i in range(start, end + 1):
        host = f"{subnet}.{i}"
        if check_printer_reachable(host, port, timeout=0.5):
            found.append({"host": host, "port": port})

    return jsonify({
        "scan_range": f"{subnet}.{start}-{end}",
        "port": port,
        "found": found,
        "scanned_at": datetime.now().isoformat()
    })


# =============================================================================
# API ENDPOINTS - Print Operations
# =============================================================================

@app.route('/print/test', methods=['POST'])
@require_api_key
def api_print_test():
    """Send a test print."""
    data = request.get_json() or {}
    printer_id = data.get('printer', 'default')

    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_message(printer, "TEST PRINT", "If you see this, the printer is working!\n\nSNP Printer Service v0.2.0")
        printer.close()
        return jsonify({"success": True, "message": "Test print sent"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/message', methods=['POST'])
@require_api_key
def api_print_message():
    """
    Print a custom message.
    Body: {
        "title": "MESSAGE TITLE",
        "message": "The message content",
        "subtitle": "Optional subtitle",
        "printer": "default"
    }
    """
    data = request.get_json()
    if not data or not data.get('message'):
        return jsonify({"success": False, "error": "Message is required"}), 400

    printer_id = data.get('printer', 'default')
    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_message(
            printer,
            title=data.get('title', 'MESSAGE'),
            message=data['message'],
            subtitle=data.get('subtitle')
        )
        printer.close()
        return jsonify({"success": True, "message": "Message printed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/reminder', methods=['POST'])
@require_api_key
def api_print_reminder():
    """
    Print a staff reminder.
    Body: {
        "type": "CLOCK OUT",
        "staff": "John",
        "message": "Your shift ends in 15 minutes",
        "action_url": "https://sipnplay.cafe/staff/clock-in",
        "printer": "default"
    }
    """
    data = request.get_json()
    if not data or not data.get('message'):
        return jsonify({"success": False, "error": "Message is required"}), 400

    printer_id = data.get('printer', 'default')
    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_reminder(
            printer,
            reminder_type=data.get('type', 'REMINDER'),
            staff_name=data.get('staff'),
            message=data['message'],
            action_url=data.get('action_url')
        )
        printer.close()
        return jsonify({"success": True, "message": "Reminder printed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/task', methods=['POST'])
@require_api_key
def api_print_task():
    """
    Print a task notification.
    Body: {
        "title": "Clean espresso machine",
        "description": "Weekly deep clean required",
        "due": "17:00",
        "priority": "high",
        "assignee": "John",
        "printer": "default"
    }
    """
    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({"success": False, "error": "Title is required"}), 400

    printer_id = data.get('printer', 'default')
    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_task(
            printer,
            task_title=data['title'],
            task_description=data.get('description', ''),
            due_time=data.get('due'),
            priority=data.get('priority'),
            assignee=data.get('assignee')
        )
        printer.close()
        return jsonify({"success": True, "message": "Task printed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# WEBHOOK ENDPOINTS (Simpler interface for integrations)
# =============================================================================

@app.route('/webhook/message', methods=['POST'])
@require_api_key
def webhook_message():
    """Simple webhook for printing messages. Same as /print/message."""
    return api_print_message()


@app.route('/webhook/reminder', methods=['POST'])
@require_api_key
def webhook_reminder():
    """Simple webhook for printing reminders. Same as /print/reminder."""
    return api_print_reminder()


@app.route('/webhook/clock-reminder', methods=['POST'])
@require_api_key
def webhook_clock_reminder():
    """
    Specialized webhook for clock-out reminders.
    Body: {
        "staff": "John",
        "shift_end": "17:00"
    }
    """
    data = request.get_json() or {}
    staff = data.get('staff', 'Staff')
    shift_end = data.get('shift_end', 'soon')

    printer = get_printer()
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_reminder(
            printer,
            reminder_type="CLOCK OUT",
            staff_name=staff,
            message=f"Your shift ends at {shift_end}.\n\nDon't forget to clock out!",
            action_url="https://sipnplay.cafe/staff/clock-in"
        )
        printer.close()
        return jsonify({"success": True, "message": "Clock reminder printed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5000'))

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║           SNP PRINTER SERVICE v0.2.0                      ║
╠═══════════════════════════════════════════════════════════╣
║  Server:      http://{host}:{port:<5}                          ║
║  Printer:     {NETWORK_HOST}:{NETWORK_PORT:<5}                        ║
║  Auth:        {'ENABLED' if REQUIRE_AUTH else 'DISABLED':<10}                             ║
║  Debug:       {str(DEBUG):<10}                             ║
╚═══════════════════════════════════════════════════════════╝
    """)

    # Check printer on startup
    if check_printer_reachable(NETWORK_HOST, NETWORK_PORT):
        print(f"[OK] Printer at {NETWORK_HOST}:{NETWORK_PORT} is reachable")
    else:
        print(f"[WARN] Printer at {NETWORK_HOST}:{NETWORK_PORT} is NOT reachable")

    app.run(host=host, port=port, debug=DEBUG)
