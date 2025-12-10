# SNP Printer Service

A Flask service for printing to network ESC/POS thermal printers with web-based management UI. Designed for Unraid deployment via Docker.

**Live URL**: https://printer.sipnplay.cafe
**Local URL**: http://192.168.50.109:5001

## Features

- **Web-based Management UI** - Configure printers, send messages, monitor health
- **Multi-Printer Support** - Add/remove printers via web interface
- **Network Discovery** - Scan for printers on your network
- **Health Monitoring** - Background monitoring with offline alerts
- **Notifications** - Discord webhook and Pushover alerts when printers go offline
- **Authentication** - Basic Auth for browser + API key for programmatic access
- **Booking Prints** - Print booking details + table marker cards
- **Buzzer Support** - Beep alerts when prints arrive

## Quick Start

### 1. Find Your Printer's IP Address

Your POS thermal printers should be connected via Ethernet and have an IP address on your network. Common ways to find it:

- Check printer's network settings menu
- Look at your router's DHCP client list
- Use the discovery feature in the web UI after starting the service

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Run with Docker Compose

```bash
# Build and start
docker-compose up -d

# Check logs
docker-compose logs -f

# Stop
docker-compose down
```

### 4. Access Web UI

Open `http://localhost:5000` in your browser (requires login).

**Default credentials:**
- Username: `admin`
- Password: `sipnplay2025`

## Pre-configured Printers

The service comes with two pre-configured printers for Sip & Play:

| ID | IP Address | Name | Location |
|----|------------|------|----------|
| `bar` | `192.168.50.200` | Bar | Front counter |
| `kitchen` | `192.168.50.103` | Kitchen | Kitchen area |

Additional printers can be added via the web UI or API.

## API Endpoints

### Public (No Auth)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |

### Browser Auth (Basic Auth)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/printer/status` | GET | All printer statuses |
| `/printer/ping` | GET | Ping specific printer |
| `/printer/discover` | GET | Scan network for printers |
| `/config/printers` | GET | Get printer configs |
| `/config/printer` | POST | Add new printer |
| `/config/printer/<id>` | DELETE | Remove printer |
| `/config/beep` | GET/POST | Beep/buzzer settings |
| `/config/notifications` | GET/POST | Notification settings |
| `/monitoring/status` | GET | Monitoring status |
| `/monitoring/toggle` | POST | Enable/disable monitoring |

### API Key Auth (for SNP-site)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/print/test` | POST | Test print |
| `/print/message` | POST | Print custom message |
| `/print/booking` | POST | Print booking ticket + table marker |
| `/print/reminder` | POST | Print staff reminder |
| `/webhook/booking` | POST | Webhook for booking prints |
| `/webhook/message` | POST | Webhook for messages |
| `/webhook/reminder` | POST | Webhook for reminders |

### Example: Print to Bar

```bash
curl -X POST https://printer.sipnplay.cafe/print/message \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "printer": "bar",
    "title": "REMINDER",
    "message": "Dont forget to clock out!"
  }'
```

### Example: Print to Kitchen

```bash
curl -X POST https://printer.sipnplay.cafe/print/message \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "printer": "kitchen",
    "title": "ORDER",
    "message": "Table 5 - 2x Coffee"
  }'
```

### Example: Print Booking

```bash
curl -X POST https://printer.sipnplay.cafe/webhook/booking \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "printer": "bar",
    "booking": {
      "name": "Bob Jane",
      "phone": "0901234567",
      "date": "Dec 10",
      "time": "18:30",
      "party_size": 5,
      "type": "Standard",
      "notes": "Birthday party"
    }
  }'
```

This prints TWO pages:
1. **Booking Details** - All info for staff reference
2. **Table Marker** - Large text with name & party size for table

## Environment Variables

### Server Configuration
| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `5000` | Server port |
| `DEBUG` | `false` | Enable debug mode |
| `CONFIG_FILE` | `/app/data/config.json` | Config file location |

### Authentication
| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_USERNAME` | `admin` | Web UI username |
| `AUTH_PASSWORD` | `sipnplay2025` | Web UI password |
| `API_KEY` | `snp-printer-secret-key-change-me` | API key for SNP-site |

### Notifications
| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_WEBHOOK_URL` | - | Discord webhook for offline alerts |
| `PUSHOVER_USER_KEY` | - | Pushover user key |
| `PUSHOVER_APP_TOKEN` | - | Pushover app token |

## Docker Deployment

### Option 1: Docker Compose (Recommended)

1. Clone this repository
2. Copy `.env.example` to `.env` and configure
3. Run `docker-compose up -d`

Configuration is persisted in the `./data` directory.

### Option 2: Docker Run

```bash
docker build -t snp-printer-service .
docker run -d \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -e AUTH_PASSWORD=your-secure-password \
  -e API_KEY=your-secure-api-key \
  snp-printer-service
```

### Option 3: Unraid

1. Go to Docker tab in Unraid
2. Click "Add Container"
3. Configure:
   - **Repository**: `ghcr.io/brendongl/snp-ticket-printer:latest`
   - **Network Type**: `bridge` (or `host` for easier discovery)
   - **Port**: `5000:5000`
   - **Path**: `/app/data` → `/mnt/user/appdata/snp-printer/`
   - **Environment Variables**: Set AUTH_PASSWORD, API_KEY

## Troubleshooting

### Printer Not Reachable

1. Verify printer IP: `ping 192.168.50.xxx`
2. Check port is open: `nc -zv 192.168.50.xxx 9100`
3. Ensure printer is on same network as server
4. Try `network_mode: host` in docker-compose.yml

### Print Quality Issues

- ESC/POS printers have 32-character line width (58mm paper)
- Use `\n` for line breaks in messages
- Keep titles short

### Connection Timeout

- Increase timeout in printer settings
- Check network congestion
- Verify printer isn't in sleep mode

### Notifications Not Working

- Verify Discord webhook URL is correct (test with curl)
- Check Pushover credentials
- View container logs: `docker-compose logs -f`

## Integration with snp-site

The admin page at `/admin/printer` in snp-site connects to this service:

1. Set `PRINTER_SERVICE_URL=https://printer.sipnplay.cafe` in snp-site's `.env`
2. Set `PRINTER_API_KEY=your-api-key`
3. Access printer controls from the admin menu

## License

MIT - Part of the Sip & Play cafe management system.
