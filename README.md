# SNP Printer Service

A minimal Flask service for printing to network ESC/POS thermal printers. Designed for Unraid deployment via Docker.

## Quick Start

### 1. Find Your Printer's IP Address

Your POS thermal printers should be connected via Ethernet and have an IP address on your network. Common ways to find it:

- Check printer's network settings menu
- Look at your router's DHCP client list
- Use the discovery endpoint after starting the service

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your printer's IP address
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

### 4. Test the Printer

```bash
# Check service health
curl http://localhost:5000/health

# Check printer status
curl http://localhost:5000/printer/status

# Send test print
curl -X POST http://localhost:5000/print/test \
  -H "Content-Type: application/json" \
  -d '{}'

# Print custom message
curl -X POST http://localhost:5000/print/message \
  -H "Content-Type: application/json" \
  -d '{"title": "REMINDER", "message": "Dont forget to clock out!"}'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info and available endpoints |
| `/health` | GET | Health check with config info |
| `/printer/status` | GET | Check if printer(s) are reachable |
| `/printer/discover` | GET | Scan network for printers |
| `/print/test` | POST | Send test print |
| `/print/message` | POST | Print custom message |

### Printer Discovery

Scan your network to find printers:

```bash
# Scan default range (192.168.1.1-50)
curl "http://localhost:5000/printer/discover"

# Custom range
curl "http://localhost:5000/printer/discover?subnet=192.168.0&start=1&end=100"
```

### Print Message

```bash
curl -X POST http://localhost:5000/print/message \
  -H "Content-Type: application/json" \
  -d '{
    "title": "CLOCK OUT REMINDER",
    "message": "Your shift ends in 15 minutes!\nDont forget to clock out."
  }'
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PRINTER_TYPE` | `network` | Printer connection type |
| `NETWORK_HOST` | `192.168.1.100` | Printer IP address |
| `NETWORK_PORT` | `9100` | Printer port (standard ESC/POS) |
| `PRINTER_HOSTS` | - | Multiple printers (comma-separated) |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `5000` | Server port |
| `DEBUG` | `false` | Enable debug mode |

## Unraid Deployment

### Option 1: Docker Compose (Recommended)

1. Copy this folder to your Unraid server
2. SSH into Unraid and navigate to the folder
3. Run `docker-compose up -d`

### Option 2: Unraid Community Applications

1. Go to Docker tab in Unraid
2. Click "Add Container"
3. Configure:
   - **Repository**: Build from this Dockerfile or push to Docker Hub
   - **Network Type**: `bridge` (or `host` for easier discovery)
   - **Port**: `5000:5000`
   - **Environment Variables**: Set `NETWORK_HOST` to your printer IP

### Option 3: Build and Push to Docker Hub

```bash
# Build image
docker build -t yourusername/snp-printer-service:latest .

# Push to Docker Hub
docker push yourusername/snp-printer-service:latest
```

Then pull from Docker Hub in Unraid.

## Troubleshooting

### Printer Not Reachable

1. Verify printer IP: `ping 192.168.1.xxx`
2. Check port is open: `nc -zv 192.168.1.xxx 9100`
3. Ensure printer is on same network as Unraid server
4. Try `network_mode: host` in docker-compose.yml

### Print Quality Issues

- ESC/POS printers have 32-character line width (58mm paper)
- Use `\n` for line breaks in messages
- Keep titles short

### Connection Timeout

- Increase timeout in `get_printer()` function
- Check network congestion
- Verify printer isn't in sleep mode

## Next Steps (Coming Soon)

- [ ] API key authentication
- [ ] Rate limiting
- [ ] Network restriction (WiFi-only)
- [ ] QR code printing
- [ ] Logo/image support
- [ ] Integration with snp-site cron jobs
- [ ] Tailscale VPN configuration

## License

MIT - Part of the Sip & Play cafe management system.
