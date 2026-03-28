# KICKR Run Speed Display

A simple Web Bluetooth app that connects to the Wahoo KICKR Run treadmill and displays speed in **mph** — something the console and Wahoo app don't offer.

## What it shows

- **Speed** in mph (large, centre screen)
- **Pace** in min/mile
- **Distance** in miles
- **Grade** percentage
- **Elapsed time**

## How to use

### iPhone / iPad
1. Install [Bluefy](https://apps.apple.com/us/app/bluefy-web-ble-browser/id1492822055) from the App Store (free)
2. Open Bluefy and go to your GitHub Pages URL
3. Tap **Connect** and select your KICKR Run

### MacBook / PC (Chrome)
1. Open the GitHub Pages URL in Chrome
2. Click **Connect** and select your KICKR Run
3. On desktop you can also click **Launch as slim panel** to open a narrow popup window you can put alongside YouTube etc.

## Setup (GitHub Pages)

1. Push this repo to GitHub
2. Go to **Settings → Pages**
3. Set source to **main** branch, root folder
4. Your site will be live at `https://yourusername.github.io/kickr-speed`

## How it works

Uses the **Bluetooth FTMS** (Fitness Machine Service) standard to read the Treadmill Data characteristic (UUID 0x2ACD) which broadcasts speed in km/h. The app converts to mph and calculates pace.

No server, no backend, no dependencies — just a single HTML file.

## Requirements

- Wahoo KICKR Run (or any FTMS-compatible treadmill)
- Chrome, Edge, or Bluefy browser (Safari does not support Web Bluetooth)
- HTTPS connection (GitHub Pages provides this)
