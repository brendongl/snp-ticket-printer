"""
SNP Printer Service v0.7.0
Flask service for printing to network ESC/POS thermal printers.
Features: Web UI, discovery, monitoring, Discord/Pushover notifications, booking prints.
"""

import os
import io
import json
import socket
import time
import threading
import requests
import hashlib
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, Response

try:
    from escpos.printer import Network
except ImportError:
    Network = None
    print("[WARN] python-escpos not installed, printer functions will be simulated")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None
    print("[WARN] Pillow not installed, image-based text printing will be disabled")

app = Flask(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

VERSION = "0.8.0"
CONFIG_FILE = os.getenv('CONFIG_FILE', '/app/data/config.json')
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'

# Default printers - Bar and Kitchen (IPs are editable via config or API)
DEFAULT_PRINTERS = {
    'bar': {'host': '192.168.50.200', 'port': 9100, 'name': 'Bar'},
    'kitchen': {'host': '192.168.50.103', 'port': 9100, 'name': 'Kitchen'},
}

# Authentication - Required since exposed to internet
AUTH_USERNAME = os.getenv('AUTH_USERNAME', 'admin')
AUTH_PASSWORD = os.getenv('AUTH_PASSWORD', 'sipnplay2025')
API_KEY = os.getenv('API_KEY', 'snp-printer-secret-key-change-me')

# Rate limiting
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 30
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
        'printer_states': {}
    },
    'beep': {
        'enabled': True,
        'times': 2,
        'duration': 2
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
                if 'printers' in saved:
                    config['printers'].update(saved.get('printers', {}))
                if 'notifications' in saved:
                    config['notifications'].update(saved.get('notifications', {}))
                if 'monitoring' in saved:
                    config['monitoring'].update(saved.get('monitoring', {}))
                if 'beep' in saved:
                    config['beep'].update(saved.get('beep', {}))
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
# AUTHENTICATION
# =============================================================================

def check_auth(username, password):
    """Check if username/password is valid."""
    return username == AUTH_USERNAME and password == AUTH_PASSWORD


def check_api_key(key):
    """Check if API key is valid."""
    return secrets.compare_digest(key, API_KEY)


def authenticate():
    """Send 401 response for Basic Auth."""
    return Response(
        'Authentication required. Please login.',
        401,
        {'WWW-Authenticate': 'Basic realm="SNP Printer Service"'}
    )


def require_auth(f):
    """Decorator requiring Basic Auth or API key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check API key first (for programmatic access)
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if api_key and check_api_key(api_key):
            if not check_rate_limit(api_key):
                return jsonify({'error': 'Rate limit exceeded'}), 429
            return f(*args, **kwargs)

        # Check Basic Auth (for browser access)
        auth = request.authorization
        if auth and check_auth(auth.username, auth.password):
            if not check_rate_limit(auth.username):
                return jsonify({'error': 'Rate limit exceeded'}), 429
            return f(*args, **kwargs)

        return authenticate()
    return decorated


def require_api_key(f):
    """Decorator requiring only API key (for webhooks from SNP-site)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')

        if not api_key or not check_api_key(api_key):
            return jsonify({'error': 'Invalid API key'}), 401

        if not check_rate_limit(api_key):
            return jsonify({'error': 'Rate limit exceeded'}), 429

        return f(*args, **kwargs)
    return decorated


# =============================================================================
# HELPERS
# =============================================================================

def get_printer(printer_id='bar'):
    """Get a network printer connection."""
    if Network is None:
        print(f"[SIMULATE] Would connect to printer: {printer_id}")
        return None

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
                "color": 15158332,
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
                "priority": 1
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

        if prev_state is True and is_online is False:
            message = f"Printer **{name}** ({host}:{port}) is now OFFLINE!"
            send_notification(message, "Printer Offline Alert")
            print(f"[MONITOR] {name} went OFFLINE - notification sent")

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
# TEXT TO IMAGE (for larger text on thermal printers)
# =============================================================================

# Standard 58mm thermal printer width in pixels (assuming 203 DPI)
PRINTER_WIDTH_PX = 384  # 48mm printable area × 8 dots/mm

def create_text_image(text, font_size=80, bold=True, max_width=PRINTER_WIDTH_PX):
    """Create a monochrome image from text for thermal printing.

    Args:
        text: Text to render
        font_size: Font size in pixels (larger = bigger text)
        bold: Use bold font if available
        max_width: Maximum width in pixels (default: 384 for 58mm printer)

    Returns:
        PIL Image object (mode='1' for monochrome)
    """
    if Image is None or ImageDraw is None:
        return None

    # Try to use a bold font, fall back to default
    font = None
    font_paths = [
        # Windows fonts
        "C:/Windows/Fonts/arialbd.ttf",  # Arial Bold
        "C:/Windows/Fonts/arial.ttf",     # Arial
        "C:/Windows/Fonts/impact.ttf",    # Impact (very bold)
        # Linux fonts
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # Container fonts
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]

    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except (OSError, IOError):
            continue

    if font is None:
        # Fall back to default font (smaller but guaranteed to work)
        font = ImageFont.load_default()
        print("[WARN] Using default font - text may be small")

    # Create temporary image to measure text size
    temp_img = Image.new('1', (1, 1), color=1)
    temp_draw = ImageDraw.Draw(temp_img)

    # Get text bounding box
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Add padding
    padding = 10
    img_width = max(text_width + padding * 2, max_width)
    img_height = text_height + padding * 2

    # Create final image (white background)
    img = Image.new('1', (img_width, img_height), color=1)
    draw = ImageDraw.Draw(img)

    # Center the text
    x = (img_width - text_width) // 2
    y = padding

    # Draw text (black on white)
    draw.text((x, y), text, font=font, fill=0)

    return img


def print_text_as_image(printer, text, font_size=80, center=True):
    """Print text as an image for maximum size compatibility.

    Args:
        printer: The printer instance
        text: Text to print
        font_size: Font size (default 80 for large visible text)
        center: Whether to center the image
    """
    if Image is None:
        # Fall back to regular text
        printer.set(align='center' if center else 'left', bold=True, width=2, height=2)
        printer.text(f"{text}\n")
        return

    img = create_text_image(text, font_size=font_size)
    if img:
        try:
            printer.image(img, center=center)
        except Exception as e:
            print(f"[WARN] Failed to print image: {e}, falling back to text")
            printer.set(align='center' if center else 'left', bold=True)
            printer.text(f"{text}\n")
    else:
        printer.set(align='center' if center else 'left', bold=True)
        printer.text(f"{text}\n")


def format_time_ampm(time_str):
    """Convert time string to 12h AM/PM format.

    Args:
        time_str: Time in various formats:
            - 24h: "17:30", "17:30:00"
            - 12h with marker: "5:30 PM", "5:30PM", "5:30 pm"

    Returns:
        Time in format like "7pm", "11am", "12:30pm"
    """
    if not time_str:
        return ""

    try:
        time_str = str(time_str).strip()
        time_upper = time_str.upper()

        # Check if input already has AM/PM marker
        has_pm = 'PM' in time_upper
        has_am = 'AM' in time_upper
        has_marker = has_pm or has_am

        # Remove AM/PM markers and clean up for parsing
        clean_time = time_upper.replace('PM', '').replace('AM', '').strip()

        # Extract hours and minutes
        parts = clean_time.replace(':', ' ').split()
        hour = int(parts[0]) if parts else 0
        minute = int(parts[1]) if len(parts) > 1 else 0

        # Determine suffix
        if has_marker:
            # Use the provided AM/PM marker
            suffix = "pm" if has_pm else "am"
            hour_12 = hour if hour != 0 else 12
            # Handle edge case: "12:30 AM" should stay 12, "12:30 PM" should stay 12
            if hour == 12:
                hour_12 = 12
        else:
            # Convert from 24h format
            if hour == 0:
                hour_12 = 12
                suffix = "am"
            elif hour < 12:
                hour_12 = hour
                suffix = "am"
            elif hour == 12:
                hour_12 = 12
                suffix = "pm"
            else:
                hour_12 = hour - 12
                suffix = "pm"

        # Format output - only show minutes if not :00
        if minute == 0:
            return f"{hour_12}{suffix}"
        else:
            return f"{hour_12}:{minute:02d}{suffix}"
    except (ValueError, IndexError):
        return time_str  # Return original if parsing fails


def expand_booking_type(booking_type):
    """Expand booking type abbreviation to full name.

    Args:
        booking_type: Type code (BG, VG, BG+VG, etc.)

    Returns:
        Full name for display
    """
    if not booking_type:
        return ""

    type_str = str(booking_type).upper().strip()

    type_map = {
        'BG': 'Board Games',
        'VG': 'Video Games',
        'BG+VG': 'Board + Video Games',
        'BGVG': 'Board + Video Games',
        'BG VG': 'Board + Video Games',
        'BOARD': 'Board Games',
        'VIDEO': 'Video Games',
        'BOARD GAMES': 'Board Games',
        'VIDEO GAMES': 'Video Games',
    }

    return type_map.get(type_str, booking_type)


def create_landscape_ticket(name, pax, time_str, booking_type=None, font_size=55):
    """Create a rotated landscape ticket image for table markers.

    Creates a compact ticket with:
      - Line 1: Name (largest)
      - Line 2: Xpax
      - Line 3: Booking type (e.g., Board Games)
      - Line 4: Time (e.g., 5:30pm)

    Then rotates 90 degrees for landscape printing.

    Args:
        name: Customer name (will be uppercased)
        pax: Party size (number of guests)
        time_str: Booking time (will be converted to AM/PM)
        booking_type: Type of booking (BG, VG, BG+VG)
        font_size: Main font size (default 55 for good visibility)

    Returns:
        PIL Image rotated 90 degrees for landscape orientation, or None if Pillow unavailable
    """
    if Image is None or ImageDraw is None or ImageFont is None:
        return None

    # Load fonts
    font = None
    medium_font = None
    small_font = None
    font_paths = [
        # Windows fonts
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        # Linux/Docker fonts
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]

    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, font_size)
            medium_font = ImageFont.truetype(font_path, int(font_size * 0.6))
            small_font = ImageFont.truetype(font_path, int(font_size * 0.5))
            break
        except (OSError, IOError):
            continue

    if font is None:
        font = ImageFont.load_default()
        medium_font = font
        small_font = font
        print("[WARN] Using default font for landscape ticket")

    # Build text lines
    name_text = name.upper() if name else "GUEST"
    pax_text = f"{pax}pax"
    type_text = expand_booking_type(booking_type) if booking_type else ""
    time_text = format_time_ampm(time_str) if time_str else ""

    # Measure text dimensions
    temp_img = Image.new('1', (1, 1), 1)
    temp_draw = ImageDraw.Draw(temp_img)

    # Line 1: Name (largest)
    name_bbox = temp_draw.textbbox((0, 0), name_text, font=font)
    name_w = name_bbox[2] - name_bbox[0]
    name_h = name_bbox[3] - name_bbox[1]

    # Line 2: Xpax (medium)
    pax_bbox = temp_draw.textbbox((0, 0), pax_text, font=medium_font)
    pax_w = pax_bbox[2] - pax_bbox[0]
    pax_h = pax_bbox[3] - pax_bbox[1]

    # Line 3: Booking type (small)
    type_w, type_h = 0, 0
    if type_text:
        type_bbox = temp_draw.textbbox((0, 0), type_text, font=small_font)
        type_w = type_bbox[2] - type_bbox[0]
        type_h = type_bbox[3] - type_bbox[1]

    # Line 4: Time (small)
    time_w, time_h = 0, 0
    if time_text:
        time_bbox = temp_draw.textbbox((0, 0), time_text, font=small_font)
        time_w = time_bbox[2] - time_bbox[0]
        time_h = time_bbox[3] - time_bbox[1]

    # Calculate image dimensions with padding
    padding = 20
    line_spacing = 8
    img_width = max(name_w, pax_w, type_w, time_w) + padding * 2

    # Total height: name + pax + optional type + optional time
    img_height = padding * 2 + name_h + line_spacing + pax_h
    if type_text:
        img_height += type_h + line_spacing
    if time_text:
        img_height += time_h + line_spacing

    # Create image (white background, monochrome)
    img = Image.new('1', (img_width, img_height), 1)
    draw = ImageDraw.Draw(img)

    # Track current Y position
    y_pos = padding

    # Line 1: Name (largest, centered)
    x1 = (img_width - name_w) // 2
    draw.text((x1, y_pos), name_text, font=font, fill=0)
    y_pos += name_h + line_spacing

    # Line 2: Xpax (medium, centered)
    x2 = (img_width - pax_w) // 2
    draw.text((x2, y_pos), pax_text, font=medium_font, fill=0)
    y_pos += pax_h + line_spacing

    # Line 3: Booking type (small, centered)
    if type_text:
        x3 = (img_width - type_w) // 2
        draw.text((x3, y_pos), type_text, font=small_font, fill=0)
        y_pos += type_h + line_spacing

    # Line 4: Time (small, centered)
    if time_text:
        x4 = (img_width - time_w) // 2
        draw.text((x4, y_pos), time_text, font=small_font, fill=0)

    # Rotate 90 degrees for landscape orientation
    img = img.rotate(90, expand=True)

    return img


# =============================================================================
# PRINT TEMPLATES
# =============================================================================

def print_header(printer, title="SIP & PLAY"):
    """Print standard header."""
    printer.set(align='center', bold=True, width=2, height=2)
    printer.text(f"{title}\n")
    printer.set(align='center', bold=False, width=1, height=1)
    printer.text("=" * 32 + "\n")


def print_footer(printer, cut=True, beep=True):
    """Print standard footer with optional beep and cut."""
    printer.set(align='center', width=1, height=1)
    printer.text("-" * 32 + "\n")
    printer.text(f"{datetime.now().strftime('%H:%M %d/%m/%Y')}\n")
    printer.text("-" * 32 + "\n\n")

    # Beep if enabled
    if beep and config['beep']['enabled']:
        try:
            printer.buzzer(times=config['beep']['times'], duration=config['beep']['duration'])
        except Exception as e:
            print(f"[WARN] Buzzer not supported: {e}")

    if cut:
        printer.cut()


def print_message(printer, title, message, subtitle=None, beep=True):
    """Print a formatted message."""
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
    print_footer(printer, beep=beep)


def print_booking(printer, booking, beep=True, name_only=False):
    """Print a booking ticket with all available fields, plus a table marker page.

    Args:
        printer: The printer instance
        booking: Booking data dictionary
        beep: Whether to beep after printing
        name_only: If True, only print page 2 (name ticket for table)
    """

    # Extract field values needed for page 2 (always needed)
    name = booking.get('customer_name') or booking.get('name') or ''
    party_size = booking.get('party_size') or booking.get('guests') or ''
    time_val = booking.get('time') or booking.get('start_time') or ''
    booking_type = booking.get('booking_type') or booking.get('type') or ''

    # Skip page 1 if name_only is True
    if not name_only:
        # === PAGE 1: Booking Details ===
        printer.set(align='center', bold=True, width=1, height=1)
        printer.text("=" * 32 + "\n")
        printer.set(width=2, height=2)
        printer.text("BOOKING\n")
        printer.set(width=1, height=1)
        printer.text("=" * 32 + "\n\n")

        printer.set(align='left', bold=False)

        # Extract additional field values for page 1
        phone = booking.get('phone') or booking.get('customer_phone') or ''
        email = booking.get('email') or booking.get('customer_email') or ''
        date = booking.get('date') or ''
        end_time = booking.get('end_time') or ''
        # booking_type already extracted above for page 2
        table = booking.get('table') or booking.get('table_number') or ''
        room = booking.get('room') or ''
        duration = booking.get('duration') or ''
        status = booking.get('status') or ''
        source = booking.get('source') or ''
        deposit = booking.get('deposit') or ''
        total = booking.get('total') or booking.get('amount') or ''

        # Print fields in pairs (side by side where possible)
        def print_field_pair(label1, val1, label2=None, val2=None):
            if val1 and str(val1).strip():
                printer.set(bold=True)
                printer.text(f"{label1}: ")
                printer.set(bold=False)
                printer.text(f"{val1}")
                if label2 and val2 and str(val2).strip():
                    printer.text(" | ")
                    printer.set(bold=True)
                    printer.text(f"{label2}: ")
                    printer.set(bold=False)
                    printer.text(f"{val2}")
                printer.text("\n")
            elif label2 and val2 and str(val2).strip():
                printer.set(bold=True)
                printer.text(f"{label2}: ")
                printer.set(bold=False)
                printer.text(f"{val2}\n")

        # Row 1: Name | Phone
        print_field_pair("Name", name, "Phone", phone)

        # Row 2: Email (full width if present)
        if email and str(email).strip():
            printer.set(bold=True)
            printer.text("Email: ")
            printer.set(bold=False)
            printer.text(f"{email}\n")

        # Row 3: Date | Time
        print_field_pair("Date", date, "Time", time_val)

        # Row 4: End Time | Duration
        print_field_pair("End", end_time, "Duration", duration)

        # Row 5: Party Size | Table
        print_field_pair("Guests", party_size, "Table", table)

        # Row 6: Type | Room
        print_field_pair("Type", booking_type, "Room", room)

        # Row 7: Status | Source
        print_field_pair("Status", status, "Source", source)

        # Row 8: Deposit | Total
        print_field_pair("Deposit", deposit, "Total", total)

        # Notes section (larger, more prominent)
        notes = booking.get('notes') or booking.get('special_requests') or booking.get('comments')
        if notes and str(notes).strip():
            printer.text("\n")
            printer.text("-" * 32 + "\n")
            printer.set(bold=True)
            printer.text("NOTES:\n")
            printer.set(bold=False)
            for line in str(notes).split('\n'):
                printer.text(f"{line}\n")
            printer.text("-" * 32 + "\n")

        printer.text("\n")
        print_footer(printer, beep=beep)

    # === PAGE 2: Table Marker (LANDSCAPE IMAGE-BASED) ===
    # Using rotated image rendering for compact landscape tickets on XPRINTER

    # Create landscape ticket image
    display_name = str(name).upper() if name else "GUEST"
    ticket_img = create_landscape_ticket(
        display_name,
        str(party_size) if party_size else "?",
        str(time_val) if time_val else "",
        booking_type=str(booking_type) if booking_type else None
    )

    # Print header line
    printer.set(align='center')
    printer.text("=" * 32 + "\n")

    # Print the rotated landscape ticket
    if ticket_img:
        try:
            printer.image(ticket_img, center=True)
        except Exception as e:
            # Fallback to text if image fails
            print(f"[WARN] Image print failed: {e}")
            printer.set(align='center', bold=True, width=2, height=2)
            printer.text(f"{display_name}\n")
            printer.set(width=1, height=1, bold=True)
            printer.text(f"{party_size}pax\n")
            if booking_type:
                printer.set(bold=False)
                printer.text(f"{expand_booking_type(booking_type)}\n")
            if time_val:
                printer.set(bold=False)
                printer.text(f"{format_time_ampm(time_val)}\n")
    else:
        # Fallback if Pillow not available
        printer.set(align='center', bold=True, width=2, height=2)
        printer.text(f"{display_name}\n")
        printer.set(width=1, height=1, bold=True)
        printer.text(f"{party_size}pax\n")
        if booking_type:
            printer.set(bold=False)
            printer.text(f"{expand_booking_type(booking_type)}\n")
        if time_val:
            printer.set(bold=False)
            printer.text(f"{format_time_ampm(time_val)}\n")

    # Print footer line
    printer.text("=" * 32 + "\n\n")

    # Cut and beep for name_only mode
    if name_only and beep:
        try:
            if config.get('beep', {}).get('enabled', True):
                printer.buzzer(times=config['beep'].get('times', 1), duration=config['beep'].get('duration', 100))
        except Exception:
            pass
    printer.cut()


def print_reminder(printer, reminder_type, staff_name, message, action_url=None, beep=True):
    """Print a staff reminder."""
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
        try:
            printer.qr(action_url, size=6)
        except Exception:
            printer.text(f"{action_url}\n")
        printer.text("\n")
    print_footer(printer, beep=beep)


# =============================================================================
# WEB UI
# =============================================================================

@app.route('/')
@require_auth
def index():
    """Serve the web UI."""
    return render_template('index.html')


# =============================================================================
# API - Health & Status (No auth required)
# =============================================================================

@app.route('/health')
def health():
    """Health check endpoint (no auth for monitoring)."""
    return jsonify({
        "status": "healthy",
        "version": VERSION,
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/printer/status')
@require_auth
def printer_status():
    """Get status of all configured printers."""
    results = []
    for name, printer_config in config['printers'].items():
        reachable = check_printer_reachable(printer_config['host'], printer_config['port'])
        results.append({
            "id": name,
            "name": printer_config.get('name', name),
            "host": printer_config['host'],
            "port": printer_config['port'],
            "status": "online" if reachable else "offline"
        })
    return jsonify({"printers": results, "checked_at": datetime.now().isoformat()})


@app.route('/printer/ping')
@require_auth
def ping_printer():
    """Ping a specific printer."""
    host = request.args.get('host')
    port = request.args.get('port', 9100)

    if not host:
        return jsonify({"error": "Host is required"}), 400

    reachable = check_printer_reachable(host, int(port))
    return jsonify({"host": host, "port": port, "reachable": reachable})


@app.route('/printer/discover')
@require_auth
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
# API - Print Operations (Requires API Key for SNP-site)
# =============================================================================

@app.route('/print/test', methods=['POST'])
@require_auth  # Changed from require_api_key to allow Basic Auth from web UI
def api_print_test():
    """Send a test print."""
    data = request.get_json() or {}
    printer_id = data.get('printer', 'bar')
    beep = data.get('beep', True)

    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_message(
            printer,
            "TEST PRINT",
            f"Printer: {printer_id}\nService: v{VERSION}\n\nIf you see this, it works!",
            beep=beep
        )
        printer.close()
        return jsonify({"success": True, "message": "Test print sent"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/test-image', methods=['POST'])
@require_auth
def api_print_test_image():
    """Test image-based text printing for larger text support."""
    data = request.get_json() or {}
    printer_id = data.get('printer', 'bar')
    text = data.get('text', 'TEST')
    font_size = data.get('font_size', 100)
    beep = data.get('beep', True)

    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        printer.set(align='center', bold=True)
        printer.text("=" * 32 + "\n")
        printer.text("IMAGE TEXT TEST\n")
        printer.text("=" * 32 + "\n\n")

        # Print test text as image
        print_text_as_image(printer, text, font_size=font_size, center=True)
        printer.text("\n")

        # Print some comparison info
        printer.set(align='center', width=1, height=1)
        printer.text(f"Font size: {font_size}px\n")
        printer.text(f"v{VERSION}\n")
        printer.text("=" * 32 + "\n\n")

        # Beep if enabled
        if beep and config['beep']['enabled']:
            try:
                printer.buzzer(times=config['beep']['times'], duration=config['beep']['duration'])
            except Exception:
                pass

        printer.cut()
        printer.close()
        return jsonify({"success": True, "message": f"Image text test printed: '{text}' at {font_size}px"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/message', methods=['POST'])
@require_auth  # Changed from require_api_key to allow Basic Auth from web UI
def api_print_message():
    """Print a custom message."""
    data = request.get_json()
    if not data or not data.get('message'):
        return jsonify({"success": False, "error": "Message is required"}), 400

    printer_id = data.get('printer', 'bar')
    beep = data.get('beep', True)

    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_message(
            printer,
            title=data.get('title', 'MESSAGE'),
            message=data['message'],
            subtitle=data.get('subtitle'),
            beep=beep
        )
        printer.close()
        return jsonify({"success": True, "message": "Message printed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/booking', methods=['POST'])
@require_api_key
def api_print_booking():
    """Print a booking ticket.

    Body params:
        booking: dict - Booking data (required)
        printer: str - Target printer (default: 'bar')
        beep: bool - Enable buzzer (default: True)
        name_only: bool - Print only the name ticket (page 2), not full booking details (default: False)
    """
    data = request.get_json()
    if not data or not data.get('booking'):
        return jsonify({"success": False, "error": "Booking data is required"}), 400

    printer_id = data.get('printer', 'bar')
    beep = data.get('beep', True)
    name_only = data.get('name_only', False)

    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_booking(printer, data['booking'], beep=beep, name_only=name_only)
        printer.close()
        message = "Name ticket printed" if name_only else "Booking printed"
        return jsonify({"success": True, "message": message})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/print/reminder', methods=['POST'])
@require_auth  # Changed from require_api_key to allow Basic Auth from web UI
def api_print_reminder():
    """Print a staff reminder."""
    data = request.get_json()
    if not data or not data.get('message'):
        return jsonify({"success": False, "error": "Message is required"}), 400

    printer_id = data.get('printer', 'bar')
    beep = data.get('beep', True)

    printer = get_printer(printer_id)
    if not printer:
        return jsonify({"success": False, "error": "Printer not available"}), 503

    try:
        print_reminder(
            printer,
            reminder_type=data.get('type', 'REMINDER'),
            staff_name=data.get('staff'),
            message=data['message'],
            action_url=data.get('action_url'),
            beep=beep
        )
        printer.close()
        return jsonify({"success": True, "message": "Reminder printed"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# API - Configuration (Requires Auth)
# =============================================================================

@app.route('/config/printers', methods=['GET'])
@require_auth
def get_printers_config():
    """Get all printer configurations."""
    return jsonify({"printers": config['printers']})


@app.route('/config/printer', methods=['POST'])
@require_auth
def add_printer():
    """Add a new printer."""
    data = request.get_json()
    if not data or not data.get('id') or not data.get('host'):
        return jsonify({"success": False, "error": "ID and host are required"}), 400

    printer_id = data['id'].lower().replace(' ', '_')
    config['printers'][printer_id] = {
        'host': data['host'],
        'port': int(data.get('port', 9100)),
        'name': data.get('name', printer_id.title())
    }
    save_config()
    return jsonify({"success": True, "message": f"Printer '{printer_id}' added"})


@app.route('/config/printer/<printer_id>', methods=['PUT'])
@require_auth
def update_printer(printer_id):
    """Update an existing printer."""
    if printer_id not in config['printers']:
        return jsonify({"success": False, "error": "Printer not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    # Update allowed fields
    if 'host' in data:
        config['printers'][printer_id]['host'] = data['host']
    if 'port' in data:
        config['printers'][printer_id]['port'] = int(data['port'])
    if 'name' in data:
        config['printers'][printer_id]['name'] = data['name']

    save_config()
    return jsonify({
        "success": True,
        "message": f"Printer '{printer_id}' updated",
        "printer": config['printers'][printer_id]
    })


@app.route('/config/printer/<printer_id>', methods=['DELETE'])
@require_auth
def remove_printer(printer_id):
    """Remove a printer."""
    if printer_id in config['printers']:
        del config['printers'][printer_id]
        save_config()
        return jsonify({"success": True, "message": f"Printer '{printer_id}' removed"})

    return jsonify({"success": False, "error": "Printer not found"}), 404


@app.route('/config/beep', methods=['GET', 'POST'])
@require_auth
def beep_settings():
    """Get or update beep settings."""
    if request.method == 'GET':
        return jsonify(config['beep'])

    data = request.get_json() or {}
    if 'enabled' in data:
        config['beep']['enabled'] = bool(data['enabled'])
    if 'times' in data:
        config['beep']['times'] = int(data['times'])
    if 'duration' in data:
        config['beep']['duration'] = int(data['duration'])

    save_config()
    return jsonify({"success": True, "beep": config['beep']})


@app.route('/config/notifications', methods=['GET', 'POST'])
@require_auth
def notification_settings():
    """Get or update notification settings."""
    if request.method == 'GET':
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
@require_auth
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
@require_auth
def monitoring_status():
    """Get monitoring status."""
    return jsonify({
        "enabled": config['monitoring']['enabled'],
        "interval": config['monitoring']['interval'],
        "last_check": config['monitoring']['last_check'],
        "printer_states": config['monitoring']['printer_states']
    })


@app.route('/monitoring/toggle', methods=['POST'])
@require_auth
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
# WEBHOOKS (for SNP-site integration)
# =============================================================================

@app.route('/webhook/booking', methods=['POST'])
@require_api_key
def webhook_booking():
    """Webhook for printing booking notifications from SNP-site."""
    return api_print_booking()


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
║  Auth:        ENABLED (Basic + API Key)                   ║
║  Beep:        {'ENABLED' if config['beep']['enabled'] else 'DISABLED'}                                    ║
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
