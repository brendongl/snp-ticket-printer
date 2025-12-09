"""
SNP Printer Service v0.3.0
Flask service for printing to network ESC/POS thermal printers.
Features: Web UI, discovery, monitoring, Discord/Pushover notifications.
"""

import os
import json
import socket
import time
import threading
import requests
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template
from escpos.printer import Network

app = Flask(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

VERSION = "0.4.0"
CONFIG_FILE = os.getenv('CONFIG_FILE', '/app/data/config.json')
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'

# Default printers (can be overridden by config)
DEFAULT_PRINTERS = {
    'pos1': {'host': '192.168.50.103', 'port': 9100, 'name': 'POS Printer 1'},
    'pos2': {'host': '192.168.50.200', 'port': 9100, 'name': 'POS Printer 2 (Q80C)'},
}

# API Security
API_KEYS = {
    'snp-site': os.getenv('API_KEY_SNP_SITE', 'dev-key-snp-site'),
    'admin': os.getenv('API_KEY_ADMIN', 'dev-key-admin'),
}
REQUIRE_AUTH = os.getenv('REQUIRE_AUTH', 'false').lower() == 'true'

# Rate limiting
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 10
rate_limit_store = {}

# Runtime configuration (loaded from file or defaults)
config = {
    'printers': DEFAULT_PRINTERS.copy(),
    'notifications': {
        'discord_webhook': os.getenv('DISCORD_WEBHOOK_URL', ''),
        'pushover_user': os.getenv('PUSHOVER_USER_KEY', ''),
        'pushover_token': os.getenv('PUSHOVER_APP_TOKEN', ''),
    },
    'monitoring': {
        'enabled': False,
        'interval': 60,  # seconds
        'last_check': None,
        'printer_states': {}  # Track online/offline state for notifications
    }
}

# Monitoring thread
monitoring_thread = None
monitoring_stop_event = threading.Event()


# =============================================================================
# CONFIG PERSISTENCE
# =============================================================================

def load_config():
    """Load configuration from file."""
    global config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                saved = json.load(f)
                # Merge with defaults
                config['printers'].update(saved.get('printers', {}))
                config['notifications'].update(saved.get('notifications', {}))
                config['monitoring'].update(saved.get('monitoring', {}))
                print(f"[CONFIG] Loaded from {CONFIG_FILE}")
    except Exception as e:
        print(f"[CONFIG] Failed to load config: {e}")


def save_config():
    """Save configuration to file."""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2, default=str)
        print(f"[CONFIG] Saved to {CONFIG_FILE}")
    except Exception as e:
        print(f"[CONFIG] Failed to save config: {e}")


# =============================================================================
# HELPERS
# =============================================================================

def get_printer(printer_id='pos1'):
    """Get a network printer connection."""
    printer_config = config['printers'].get(printer_id)
    # Fallback to first available printer if requested one not found
    if not printer_config and config['printers']:
        printer_id = list(config['printers'].keys())[0]
        printer_config = config['printers'][printer_id]
    if not printer_config:
        return None
    try:
        printer = Network(printer_config['host'], port=printer_config['port'], timeout=10)
        return printer
    except Exception as e:
        print(f"[ERROR] Failed to connect to printer {printer_id}: {e}")
        return None


def check_printer_reachable(host, port=9100, timeout=3):
    """Check if a printer is reachable on the network."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, int(port)))
        sock.close()
        return result == 0
    except Exception:
        return False


def check_rate_limit(key):
    """Simple rate limiting."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    if key in rate_limit_store:
        rate_limit_store[key] = [t for t in rate_limit_store[key] if t > window_start]
    else:
        rate_limit_store[key] = []

    if len(rate_limit_store[key]) >= RATE_LIMIT_MAX:
        return False

    rate_limit_store[key].append(now)
    return True


# =============================================================================
# NOTIFICATIONS
# =============================================================================

def send_discord_notification(message, title="SNP Printer Alert"):
    """Send notification via Discord webhook."""
    webhook_url = config['notifications'].get('discord_webhook')
    if not webhook_url:
        return False

    try:
        payload = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": 15158332,  # Red color
                "timestamp": datetime.utcnow().isoformat()
            }]
        }
        response = requests.post(webhook_url, json=payload, timeout=10)
        return response.status_code == 204
    except Exception as e:
        print(f"[DISCORD] Failed to send notification: {e}")
        return False


def send_pushover_notification(message, title="SNP Printer Alert"):
    """Send notification via Pushover."""
    user_key = config['notifications'].get('pushover_user')
    app_token = config['notifications'].get('pushover_token')

    if not user_key or not app_token:
        return False

    try:
        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": app_token,
                "user": user_key,
                "title": title,
                "message": message,
                "priority": 1  # High priority
            },
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[PUSHOVER] Failed to send notification: {e}")
        return False


def send_notification(message, title="SNP Printer Alert"):
    """Send notification via all configured channels."""
    results = []

    if config['notifications'].get('discord_webhook'):
        results.append(('discord', send_discord_notification(message, title)))

    if config['notifications'].get('pushover_user') and config['notifications'].get('pushover_token'):
        results.append(('pushover', send_pushover_notification(message, title)))

    return results


# =============================================================================
# MONITORING
# =============================================================================

def monitoring_loop():
    """Background monitoring loop."""
    while not monitoring_stop_event.is_set():
        if config['monitoring']['enabled']:
            check_all_printers()
        monitoring_stop_event.wait(config['monitoring']['interval'])


def check_all_printers():
    """Check all printers and send notifications if status changed."""
    config['monitoring']['last_check'] = datetime.now().isoformat()

    for name, printer_config in config['printers'].items():
        host = printer_config['host']
        port = printer_config['port']
        is_online = check_printer_reachable(host, port)

        prev_state = config['monitoring']['printer_states'].get(name)
        config['monitoring']['printer_states'][name] = is_online

        # Send notification if state changed to offline
        if prev_state is True and is_online is False:
            message = f"Printer **{name}** ({host}:{port}) is now OFFLINE!"
            send_notification(message, "Printer Offline Alert")
            print(f"[MONITOR] {name} went OFFLINE - notification sent")

        # Send notification if state changed to online (recovered)
        elif prev_state is False and is_online is True:
            message = f"Printer **{name}** ({host}:{port}) is back ONLINE."
            send_notification(message, "Printer Recovered")
            print(f"[MONITOR] {name} is back ONLINE - notification sent")


def start_monitoring():
    """Start the monitoring thread."""
    global monitoring_thread
    if monitoring_thread and monitoring_thread.is_alive():
        return

    monitoring_stop_event.clear()
    monitoring_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitoring_thread.start()
    print("[MONITOR] Started monitoring thread")


def stop_monitoring():
    """Stop the monitoring thread."""
    monitoring_stop_event.set()
    print("[MONITOR] Stopped monitoring thread")


# =============================================================================
# AUTH DECORATOR
# =============================================================================

def require_api_key(f):
    """Decorator to require API key authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not REQUIRE_AUTH:
            return f(*args, **kwargs)

        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')

        if not api_key or api_key not in API_KEYS.values():
            return jsonify({'error': 'Unauthorized'}), 401

        if not check_rate_limit(api_key):
            return jsonify({'error': 'Rate limit exceeded'}), 429

        return f(*args, **kwargs)
    return decorated


# =============================================================================
# PRINT TEMPLATES
# =============================================================================

def print_header(printer, title="SIP & PLAY"):
    printer.set(align='center', bold=True, width=2, height=2)
    printer.text(f"{title}\n")
    printer.set(align='center', bold=False, width=1, height=1)
    printer.text("=" * 32 + "\n")


def print_footer(printer, cut=True):
    printer.set(align='center', width=1, height=1)
    printer.text("-" * 32 + "\n")
    printer.text(f"{datetime.now().strftime('%H:%M %d/%m/%Y')}\n")
    printer.text("-" * 32 + "\n\n")
    if cut:
        printer.cut()


def print_message(printer, title, message, subtitle=None):
    print_header(printer)
    printer.set(align='center', bold=True)
    printer.text(f"{title}\n")
    if subtitle:
        printer.set(bold=False)
        printer.text(f"{subtitle}\n")
    printer.text("=" * 32 + "\n\n")
    printer.set(align='left', bold=False)
    for line in message.split('\n'):
        printer.text(f"{line}\n")
    printer.text("\n")
    print_footer(printer)


def print_reminder(printer, reminder_type, staff_name, message, action_url=None):
    print_header(printer)
    printer.set(align='center', bold=True)
    printer.text("REMINDER\n")
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


# =============================================================================
# WEB UI
# =============================================================================

@app.route('/')
def index():
    """Serve the web UI."""
    return render_template('index.html')


# =============================================================================
# API - Health & Status
# =============================================================================

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "version": VERSION,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/printer/status')
def printer_status():
    """Get status of all configured printers."""
    results = []
    for name, printer_config in config['printers'].items():
        reachable = check_printer_reachable(printer_config['host'], printer_config['port'])
        results.append({
            "name": name,
            "host": printer_config['host'],
            "port": printer_config['port'],
            "status": "online" if reachable else "offline"
        })
    return jsonify({"printers": results, "checked_at": datetime.now().isoformat()})


@app.route('/printer/ping')
def ping_printer():
    """Ping a specific printer."""
    host = request.args.get('host')
    port = request.args.get('port', 9100)

    if not host:
        return jsonify({"error": "Host is required"}), 400

    reachable = check_printer_reachable(host, int(port))
    return jsonify({"host": host, "port": port, "reachable": reachable})


@app.route('/printer/discover')
def discover_printers():
    """Discover printers on the network."""
    subnet = request.args.get('subnet', '192.168.50')
    port = int(request.args.get('port', '9100'))
    start = int(request.args.get('start', '1'))
    end = min(int(request.args.get('end', '254')), 254)

    found = []
    for i in range(start, end + 1):
        host = f"{subnet}.{i}"
        if check_printer_reachable(host, port, timeout=0.3):
            found.append({"host": host, "port": port})

    return jsonify({
        "scan_range": f"{subnet}.{start}-{end}",
        "port": port,
        "found": found,
        "scanned_at": datetime.now().isoformat()
    })


# =============================================================================
# API - Print Operations
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
        print_message(printer, "TEST PRINT", f"Printer: {printer_id}\nService: v{VERSION}\n\nIf you see this, it works!")
        printer.close()
        return jsonify({"success": True, "message": "Test print sent"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/message', methods=['POST'])
@require_api_key
def api_print_message():
    """Print a custom message."""
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
    """Print a staff reminder."""
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


# =============================================================================
# API - Configuration
# =============================================================================

@app.route('/config/printer', methods=['POST'])
def add_printer():
    """Add a new printer."""
    data = request.get_json()
    if not data or not data.get('name') or not data.get('host'):
        return jsonify({"success": False, "error": "Name and host are required"}), 400

    name = data['name'].lower().replace(' ', '_')
    config['printers'][name] = {
        'host': data['host'],
        'port': int(data.get('port', 9100))
    }
    save_config()
    return jsonify({"success": True, "message": f"Printer '{name}' added"})


@app.route('/config/printer/<name>', methods=['DELETE'])
def remove_printer(name):
    """Remove a printer."""
    if name == 'default':
        return jsonify({"success": False, "error": "Cannot remove default printer"}), 400

    if name in config['printers']:
        del config['printers'][name]
        save_config()
        return jsonify({"success": True, "message": f"Printer '{name}' removed"})

    return jsonify({"success": False, "error": "Printer not found"}), 404


@app.route('/config/notifications', methods=['GET', 'POST'])
def notification_settings():
    """Get or update notification settings."""
    if request.method == 'GET':
        # Return settings (mask sensitive data)
        return jsonify({
            'discord_webhook': config['notifications'].get('discord_webhook', ''),
            'pushover_user': config['notifications'].get('pushover_user', ''),
            'pushover_token': '***' if config['notifications'].get('pushover_token') else '',
        })

    data = request.get_json() or {}
    if 'discord_webhook' in data:
        config['notifications']['discord_webhook'] = data['discord_webhook']
    if 'pushover_user' in data:
        config['notifications']['pushover_user'] = data['pushover_user']
    if 'pushover_token' in data and data['pushover_token'] != '***':
        config['notifications']['pushover_token'] = data['pushover_token']

    save_config()
    return jsonify({"success": True, "message": "Notification settings saved"})


@app.route('/notifications/test', methods=['POST'])
def test_notifications():
    """Send test notifications."""
    results = send_notification("This is a test notification from SNP Printer Service.", "Test Notification")

    if not results:
        return jsonify({"success": False, "message": "No notification channels configured"})

    successful = [r[0] for r in results if r[1]]
    failed = [r[0] for r in results if not r[1]]

    return jsonify({
        "success": len(successful) > 0,
        "message": f"Sent: {', '.join(successful) if successful else 'none'}. Failed: {', '.join(failed) if failed else 'none'}"
    })


# =============================================================================
# API - Monitoring
# =============================================================================

@app.route('/monitoring/status')
def monitoring_status():
    """Get monitoring status."""
    return jsonify({
        "enabled": config['monitoring']['enabled'],
        "interval": config['monitoring']['interval'],
        "last_check": config['monitoring']['last_check'],
        "printer_states": config['monitoring']['printer_states']
    })


@app.route('/monitoring/toggle', methods=['POST'])
def toggle_monitoring():
    """Enable or disable monitoring."""
    data = request.get_json() or {}
    enabled = data.get('enabled', not config['monitoring']['enabled'])

    config['monitoring']['enabled'] = enabled
    save_config()

    if enabled:
        start_monitoring()
    else:
        stop_monitoring()

    return jsonify({"success": True, "enabled": enabled})


# =============================================================================
# WEBHOOKS (for external integrations)
# =============================================================================

@app.route('/webhook/message', methods=['POST'])
@require_api_key
def webhook_message():
    """Webhook for printing messages."""
    return api_print_message()


@app.route('/webhook/reminder', methods=['POST'])
@require_api_key
def webhook_reminder():
    """Webhook for printing reminders."""
    return api_print_reminder()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '5000'))

    # Load saved configuration
    load_config()

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║           SNP PRINTER SERVICE v{VERSION}                      ║
╠═══════════════════════════════════════════════════════════╣
║  Web UI:      http://{host}:{port}/                             ║
║  Printers:    {len(config['printers'])} configured                            ║
║  Monitoring:  {'ENABLED' if config['monitoring']['enabled'] else 'DISABLED'}                                  ║
║  Auth:        {'ENABLED' if REQUIRE_AUTH else 'DISABLED'}                                   ║
╚═══════════════════════════════════════════════════════════╝
    """)

    # Start monitoring if enabled
    if config['monitoring']['enabled']:
        start_monitoring()

    # Check all printers on startup
    print("\n[STARTUP] Checking printer connectivity...")
    for printer_name, printer_cfg in config['printers'].items():
        printer_host = printer_cfg.get('host')
        printer_port = printer_cfg.get('port', 9100)
        display_name = printer_cfg.get('name', printer_name)
        if check_printer_reachable(printer_host, printer_port):
            print(f"  [OK] {display_name} ({printer_host}:{printer_port}) - ONLINE")
        else:
            print(f"  [WARN] {display_name} ({printer_host}:{printer_port}) - OFFLINE")

    app.run(host=host, port=port, debug=DEBUG, threaded=True)
