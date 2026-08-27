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
# REQUIRED: headers to build python-gphoto2 against the SYSTEM libgphoto2.
# Without these, pip falls back to the wheel — see the warning below.
sudo apt install -y libgphoto2-dev pkg-config
```
> **Note**: The Pi will need a USB printer that supports CUPS.

> ### ⚠️ python-gphoto2 must be built from source, not installed as a wheel
> The wheel bundles its own **libgphoto2 2.5.34**, and that build stalls M50 live
> view — a ~3.0s dead preview grab every ~6s, permanently. The preview worker holds
> the camera lock across each grab, so a stalled grab blocks the shutter and the
> guest's photo fires ~3s after the countdown hits zero.
>
> Measured on this rig — same code, same camera, same 60s, only the library swapped:
>
> | libgphoto2 | frames in 60s | rate | stalls | shutters blocked (6s spacing) |
> |---|---|---|---|---|
> | 2.5.34 (bundled in the wheel) | 1693 | 28.2 fps | **10** | **3/14**, mean lock wait 642 ms |
> | 2.5.30 (system, apt) | 3598 | 60.0 fps | **0** | **0/15**, mean lock wait 3 ms |
>
> `backend/requirements.txt` carries a `--no-binary gphoto2` line that forces the
> source build. **Do not remove it**, and do not `pip install gphoto2` by hand — pip
> will silently pick the wheel and the stall comes straight back. Verify after
> install:
> ```bash
> python3 -c "import gphoto2 as gp; print(gp.gp_library_version(gp.GP_VERSION_VERBOSE)[0])"
> # must NOT print 2.5.34
> ```
> Re-check any of this on the Pi (booth stopped) with:
> ```bash
> python3 backend/tools/preview_stall_probe.py --duration 60
> ```
> It prints the loaded libgphoto2 version, then polls live view. Zero stalls = a
> correct install. ~3.0s stalls arriving every ~6s = you are on the wheel.

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
pip install -r backend/requirements.txt
# Verify FastAPI start (will run on port 8000)
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
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

### Any CUPS printer
1. Open the CUPS web UI on the Pi (http://localhost:631).
2. Add your printer → **Add Printer** → select the USB device.
3. Choose the appropriate driver (most modern inkjets work with the generic **IPP Everywhere** driver).
4. Put the **queue name** into the admin panel's **Printer** tab. The booth
   selects its driver from that name; it does not need to be the system default.

### DNP DS-RX1HS (the booth's dye-sub)
```bash
sudo apt install -y printer-driver-gutenprint cups-ipp-utils
sudo systemctl restart cups
sudo usermod -aG lp,lpadmin "$USER"     # USB access without root; log out and back in
```
Then add it from the CUPS web UI with the printer **on and connected over USB**.

- It appears as **DS-RX1**, not RX1HS — the HS is a firmware and media
  revision, not a separate model to the driver.
- The driver must be **Gutenprint**, using the `gutenprint53+usb` backend, which
  exists specifically for the DS-RX1/RX1HS USB protocol. Generic USB will not do.
- `cups-ipp-utils` supplies `ipptool`, which is how the booth reads prints
  remaining. Without it everything still works; the media readout is just blank.

Then, in the admin panel's **Printer** tab:
1. Set the queue name.
2. Press **Print Alignment Card** and check it against a ruler.
3. Tune **Print options** if the geometry is wrong, and print again.

⚠️ Do not skip step 2. Gutenprint has a known "printout gets squeezed" bug on
this model, and a squeezed print is not obvious until you measure one.
**[PRINTER_NOTES.md](PRINTER_NOTES.md) has the full hardware-run checklist** —
work through it before the event, not at it.

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

## 1️⃣1️⃣ LED Ring (optional)

The ring is an ESP32 node on the booth's own Wi-Fi, driven over HTTP. The booth
runs unchanged without one — every command is a no-op when it is disabled or
unreachable, and a dead ring never stops a photo being taken. Firmware, wiring
and the command vocabulary live in `led-node/README.md`; this section is only
what the Pi side needs.

Bring it up in this order. Each step is a precondition for the next.

**1. Power, before anything else.** The strip needs its own 5 V supply — a Pi 5's
official 5 A total cannot carry the ~1.8 A the ring draws at Capture, and
browning out the Pi mid-session is a far worse failure than having no ring.
Inject power at both ends of the strip, and put a 74AHCT125 on the data line:
the ESP32 drives 3.3 V and SK6812s want 5 V logic.

**2. Wi-Fi credentials into the firmware.** Set them under `LED Node
Configuration → Command transport` in `idf.py menuconfig`. They land in
`sdkconfig`, which is gitignored. **Never** put them in `sdkconfig.defaults`,
which is tracked.

**3. Give the node a fixed address.** The Pi's config stores one address, so the
node must always get the same one. Reserve it by MAC in `dnsmasq` rather than
configuring a static IP on the node — that keeps the address visible on the Pi,
where whoever is troubleshooting at the venue can actually see it:

```bash
# Read the MAC off the node's first boot:  idf.py monitor
#   wifi: connected — open http://192.168.4.50/
# ...or, once it has associated, from the Pi:
sudo arp -a | grep -i wlan0

# /etc/dnsmasq.conf — one line, MAC to address
dhcp-host=aa:bb:cc:dd:ee:ff,192.168.4.50

sudo systemctl restart dnsmasq
```

Put the node on the booth's own AP as its only client, and hand-pick the
channel. A wedding venue is a hostile 2.4 GHz environment, and retries land
exactly in the countdown-to-shutter window (`Docs/LED_UART_SWITCH.md`).

**4. Point the booth at it.** Admin → **LEDs**: turn the ring on, enter the
address (host or IP only — no scheme, no port), then tap **Ping Node**. A healthy
node answers `PONG` in a few milliseconds. Changes apply immediately; there is no
restart.

**5. Check the strip.** Still on the LEDs tab, tap All Red, All Green, All Blue
and All White in turn and walk the ring. Each lights one physical die flat at
full brightness, so a pixel that is dead, miswired or has one channel out is
obvious — which it is not under any of the booth patterns, since they all mix
dies. Tap **Back to Idle** when done (the node returns on its own after two
minutes either way). These are refused unless the booth is on its idle screen.

**6. Confirm the card.** The LED Ring card in System → Live Diagnostics goes green and
starts reporting CAPTURE p95 once a session has run. That number is the one the
transport decision rests on — see `Docs/LED_UART_SWITCH.md` for the thresholds
that would trigger a move to UART, and record it at the venue rather than in a
quiet room.

### When the ring is stuck on the link-lost pattern

The node enters Link Lost after 10 s with no inbound line, and it recovers on
the heartbeat alone — a `PING` is enough. So:

- Restarting the backend is sufficient. No reflash, no power cycle.
- If it does not recover, the node is not receiving anything: check the Test
  Ring result, then the DHCP reservation, then that the node associated at all
  (`sudo arp -a`).
- A red card with `ERR` text in it means the opposite problem — the node is
  answering and refusing the command. That is a protocol mismatch, not a link
  fault; see `Docs/LED_PROTOCOL.md`.

## 📋 Quick Checklist
- [ ] OS updated, core packages installed
- [ ] Repository cloned
- [ ] Python venv created & dependencies installed
- [ ] Frontend built (`npm run build`)
- [ ] CUPS printer added, queue name entered in the Printer tab
- [ ] Alignment card printed and measured (see PRINTER_NOTES.md)
- [ ] `photo-booth.service` enabled & running
- [ ] Chromium kiosk autostart configured
- [ ] (Optional) VNC/Xvfb for headless operation
- [ ] (Optional) Wi‑Fi AP & captive portal configured
- [ ] (Optional) LED ring: own 5 V supply, DHCP reservation, address entered, ping green, all four dies checked

---
**You can now power the Pi, let it boot, and the photo‑booth UI will appear automatically in kiosk mode.**
