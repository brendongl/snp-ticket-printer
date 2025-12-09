# SNP Printer Service

A Flask service for printing to network ESC/POS thermal printers with web-based management UI. Designed for Unraid deployment via Docker.

## Features

- **Web-based Management UI** - Configure printers, send messages, monitor health
- **Multi-Printer Support** - Add/remove printers via web interface
- **Network Discovery** - Scan for printers on your network
- **Health Monitoring** - Background monitoring with offline alerts
- **Notifications** - Discord webhook and Pushover alerts when printers go offline
- **API Authentication** - Optional API key protection
- **Print Templates** - Message, reminder, task, and order templates

## Quick Start

### 1. Find Your Printer's IP Address

Your POS thermal printers should be connected via Ethernet and have an IP address on your network. Common ways to find it:

- Check printer's network settings menu
- Look at your router's DHCP client list
- Use the discovery feature in the web UI after starting the service

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your printer's IP address and notification settings
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

Open `http://localhost:5000` in your browser to access the management interface.

## Web UI Features

### Printer Management
- View all configured printers with online/offline status
- Add new printers by IP address
- Remove printers
- Send test prints to verify connectivity

### Printer Discovery
- Scan your network for thermal printers
- Automatically find printers on port 9100
- Add discovered printers with one click

### Send Messages
- Send custom messages to any configured printer
- Multiple templates: message, reminder, task, order
- Preview before printing

### Health Monitoring
- Enable background monitoring (configurable interval)
- Automatic notifications when printers go offline
- Real-time status updates

### Notifications
- **Discord Webhooks** - Get alerts in your Discord channel
- **Pushover** - Mobile push notifications

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/health` | GET | Health check with config info |
| `/api/status` | GET | All printer statuses |
| `/api/printer/<name>/ping` | GET | Ping specific printer |
| `/api/printer/add` | POST | Add new printer |
| `/api/printer/remove` | POST | Remove printer |
| `/api/discover` | GET | Scan network for printers |
| `/api/print` | POST | Send print job |
| `/api/config/notifications` | GET/POST | Notification settings |
| `/api/monitoring/start` | POST | Start health monitoring |
| `/api/monitoring/stop` | POST | Stop health monitoring |
| `/api/monitoring/status` | GET | Monitoring status |

### Example: Print Custom Message

```bash
# Print to POS Printer 1
curl -X POST http://localhost:5000/print/message \
  -H "Content-Type: application/json" \
  -d '{
    "printer": "pos1",
    "title": "REMINDER",
    "message": "Dont forget to clock out!"
  }'

# Print to POS Printer 2 (Xprinter Q80C)
curl -X POST http://localhost:5000/print/message \
  -H "Content-Type: application/json" \
  -d '{
    "printer": "pos2",
    "title": "KITCHEN",
    "message": "Order ready for pickup!"
  }'
```

### Example: Scan for Printers

```bash
curl "http://localhost:5000/api/discover?subnet=192.168.50&start=1&end=254"
```

## Pre-configured Printers

The service comes with two pre-configured printers for Sip & Play:

| ID | IP Address | Name | Description |
|----|------------|------|-------------|
| `pos1` | `192.168.50.103` | POS Printer 1 | Main POS printer |
| `pos2` | `192.168.50.200` | POS Printer 2 (Q80C) | Xprinter Q80C |

Additional printers can be added via the web UI or API.

## Environment Variables

### Server Configuration
| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `5000` | Server port |
| `DEBUG` | `false` | Enable debug mode |
| `CONFIG_FILE` | `/app/data/config.json` | Config file location |

### Notifications
| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_WEBHOOK_URL` | - | Discord webhook for offline alerts |
| `PUSHOVER_USER_KEY` | - | Pushover user key |
| `PUSHOVER_APP_TOKEN` | - | Pushover app token |

### Security
| Variable | Default | Description |
|----------|---------|-------------|
| `REQUIRE_AUTH` | `false` | Require API keys |
| `API_KEY_SNP_SITE` | - | API key for snp-site |
| `API_KEY_ADMIN` | - | API key for admin access |

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
  -e NETWORK_HOST=192.168.50.103 \
  snp-printer-service
```

### Option 3: Unraid

1. Go to Docker tab in Unraid
2. Click "Add Container"
3. Configure:
   - **Repository**: `ghcr.io/brendongl/snp-ticket-printer:latest` (or build locally)
   - **Network Type**: `bridge` (or `host` for easier discovery)
   - **Port**: `5000:5000`
   - **Path**: `/app/data` → `/mnt/user/appdata/snp-printer/`
   - **Environment Variables**: Configure as needed

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

The admin page at `/admin/printer` in snp-site can connect to this service:

1. Set `PRINTER_SERVICE_URL=http://your-unraid-ip:5000` in snp-site's `.env`
2. Set `PRINTER_API_KEY` if authentication is enabled
3. Access printer controls from the admin menu

## License

MIT - Part of the Sip & Play cafe management system.
