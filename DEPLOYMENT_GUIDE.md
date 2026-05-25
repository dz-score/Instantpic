# DEPLOYMENT GUIDE – Raspberry Pi 4 Photo Booth

## 1️⃣ Prerequisites (on the target Pi)
- Raspberry Pi OS (32‑bit) – **Lite** version recommended.
- Minimum 2 GB RAM, SD card ≥ 8 GB (class 10).
- Stable power supply (≥ 3 A).
- Internet connection for initial package installation.
- A monitor (or use headless mode – see section 9).

## 2️⃣ System Update & Core Packages
```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```
After reboot, install the essentials:
```bash
sudo apt install -y git python3 python3-pip python3-venv nodejs npm build-essential
# Install CUPS (printing)
sudo apt install -y cups
# Add pi to the lpadmin group so it can print
sudo usermod -aG lpadmin $(whoami)
```
> **Note**: The Pi will need a USB printer that supports CUPS.

## 3️⃣ Clone the Repository
```bash
# Choose a location for the project
mkdir -p ~/photo-booth && cd ~/photo-booth
# Replace <repo‑url> with the actual Git URL (e.g., GitHub)
git clone <repo‑url> .
```
If the repo is private, generate an SSH key on the Pi and add it to the remote host.

## 4️⃣ Python Backend Setup
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# Verify FastAPI start (will run on port 8000)
uvicorn main:app --host 0.0.0.0 --port 8000 &
```
Press **Ctrl‑C** to stop after confirming the server prints `Uvicorn running on http://0.0.0.0:8000`.

## 5️⃣ Node / React Frontend Build
```bash
cd ../frontend
npm install
npm run build   # creates ./dist folder
# Serve static files via the FastAPI backend (already configured)
```
The built assets are automatically served by the backend at `/`.

## 6️⃣ Configure CUPS Printing
1. Open the CUPS web UI on the Pi (http://localhost:631).
2. Add your printer → **Add Printer** → select the USB device.
3. Choose the appropriate driver (most modern printers work with the generic **IPP Everywhere** driver).
4. Set the default printer (required by the backend `printer.py`).
```bash
# Optional – set default printer from CLI
lpoptions -d <printer_name>
```

## 7️⃣ Systemd Service – Run backend automatically
Create `/etc/systemd/system/photo-booth.service`:
```ini
[Unit]
Description=Photo Booth FastAPI backend
After=network.target

[Service]
WorkingDirectory=/home/pi/photo-booth/backend
ExecStart=/home/pi/photo-booth/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure
User=pi
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable photo-booth.service
sudo systemctl start photo-booth.service
```
Check status with `sudo systemctl status photo-booth.service`.

## 8️⃣ Chromium Kiosk Mode (frontend UI)
```bash
# Install Chromium if not present
sudo apt install -y chromium-browser
# Create a simple .desktop file for auto‑start
cat <<EOF | sudo tee /etc/xdg/autostart/photo-booth-kiosk.desktop
[Desktop Entry]
Type=Application
Exec=chromium-browser --noerrdialogs --disable-infobars --kiosk http://localhost:8000
Hidden=false
X-GNOME-Autostart-enabled=true
Name=Photo Booth Kiosk
EOF
```
Reboot; Chromium will launch full‑screen pointing at the backend UI.

## 9️⃣ (Optional) Headless / Remote Display
If no monitor is attached, enable **Virtual Framebuffer**:
```bash
sudo apt install -y xvfb
# Modify the .desktop Exec line to launch via Xvfb
Exec=xvfb-run --auto-servernum --server-num=1 chromium-browser --noerrdialogs --disable-infobars --kiosk http://localhost:8000
```
You can then VNC into the Pi to view the UI.

## 🔟 Wi‑Fi Access Point / Captive Portal (Event Mode)
1. Install hostapd and dnsmasq:
```bash
sudo apt install -y hostapd dnsmasq
```
2. Configure `/etc/dhcpcd.conf` to assign a static IP to `wlan0`.
3. Set up `/etc/hostapd/hostapd.conf` (SSID, passphrase, 2.4 GHz). 
4. Enable IP forwarding and NAT so the Pi can reach the internet for package installs.
5. (Advanced) Use **nodogsplash** or **captive‑portal** package for a simple landing page.
> Detailed AP configuration varies per venue; copy the snippets from the repo’s `config.json` under the `wifi` section.

## 📋 Quick Checklist
- [ ] OS updated, core packages installed
- [ ] Repository cloned
- [ ] Python venv created & dependencies installed
- [ ] Frontend built (`npm run build`)
- [ ] CUPS printer added & default set
- [ ] `photo-booth.service` enabled & running
- [ ] Chromium kiosk autostart configured
- [ ] (Optional) VNC/Xvfb for headless operation
- [ ] (Optional) Wi‑Fi AP & captive portal configured

---
**You can now power the Pi, let it boot, and the photo‑booth UI will appear automatically in kiosk mode.**
