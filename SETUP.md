# PaperBrain — Setup Guide

## What this is
A Slack bot running on your M1 Mac that:
- Watches for DOIs in Slack channels → replies with chemistry summary in thread
- Responds to your DMs with ML/computational summaries
- Watches an iCloud folder for dropped PDFs
- Saves all summaries to your Obsidian vault as linked notes

---

## Step 1 — Slack App Setup

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**
2. Name it `PaperBrain`, select your **lab workspace**
3. Under **Socket Mode** (left sidebar) → Enable Socket Mode → Generate App-Level Token
   - Token name: `paperbrain-socket`
   - Scope: `connections:write`
   - Copy the `xapp-...` token → this is your `app_token` in config
4. Under **OAuth & Permissions** → Bot Token Scopes, add:
   - `channels:history`
   - `channels:read`
   - `chat:write`
   - `files:read`
   - `im:history`
   - `im:read`
   - `im:write`
   - `users:read`
5. Under **Event Subscriptions** → Enable Events → Subscribe to bot events:
   - `message.channels`
   - `message.im`
6. Under **Install App** → Install to Workspace → copy `Bot User OAuth Token` (`xoxb-...`)
7. Under **Basic Information** → App Credentials → copy **Signing Secret**

### Add to personal workspace (optional but recommended)
Repeat steps 1-6 in your personal Slack workspace. You can use the same codebase,
just add a second config section or run a second instance with different config.

### Get channel IDs
Right-click any channel → **View channel details** → scroll to bottom → copy Channel ID (`C0XXXXXXX`)

### Get your user ID  
Click your profile picture → **Profile** → three dots menu → **Copy member ID**

---

## Step 2 — Install on M1 Mac

```bash
# Clone or copy project to M1
mkdir -p ~/paperbrain
cp -r /path/to/paperbrain/* ~/paperbrain/

# Create Python virtual environment
cd ~/paperbrain
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create config from template
cp config/config.template.yaml config/config.yaml
```

---

## Step 3 — Fill in config.yaml

```bash
nano ~/paperbrain/config/config.yaml
```

Fill in:
- `slack.bot_token` — your `xoxb-...` token
- `slack.app_token` — your `xapp-...` token  
- `slack.signing_secret` — from Basic Information
- `slack.watched_channels` — list of channel IDs
- `slack.elia_user_id` — your Slack user ID
- `obsidian.vault_path` — already set correctly for your setup

---

## Step 4 — Make sure Ollama is accessible

```bash
# Edit Ollama launchd to listen on all interfaces (for Tailscale access)
# Find the plist:
ls ~/Library/LaunchAgents/ | grep ollama

# Or just set env var before running for now:
OLLAMA_HOST=0.0.0.0 ollama serve
```

---

## Step 5 — Test the pipeline manually

```bash
cd ~/paperbrain
source venv/bin/activate
cd src

# Quick test — process a DOI without Slack
python3 -c "
import yaml
from pipeline import process_doi
from summarizer import format_slack_chem_summary, format_slack_ml_summary

with open('../config/config.yaml') as f:
    config = yaml.safe_load(f)

paper, summary = process_doi('10.1021/jacs.3c01234', config)
print('=== CHEM SUMMARY ===')
print(format_slack_chem_summary(summary))
print()
print('=== ML SUMMARY ===')
print(format_slack_ml_summary(summary))
"
```

---

## Step 6 — Run the bot

```bash
cd ~/paperbrain
source venv/bin/activate
python3 src/slack_bot.py
```

Test it: DM the bot a DOI. If it responds, you're good.

---

## Step 7 — Install as autostart service

```bash
# Copy the plist (update paths inside it first if needed)
cp com.paperbrain.bot.plist ~/Library/LaunchAgents/

# Load it
launchctl load ~/Library/LaunchAgents/com.paperbrain.bot.plist

# Check it's running
launchctl list | grep paperbrain

# Watch logs
tail -f ~/paperbrain/logs/paperbrain.log
```

---

## Using it

**From anywhere (phone, laptop, wherever):**
- DM `@PaperBrain` a DOI → get ML summary + saved to Obsidian
- DM `@PaperBrain` a PDF → same
- Drop a PDF into `PaperBrain/inbox_pdfs/` in iCloud → same

**In lab Slack channels:**
- Someone posts a DOI or paper URL → bot replies in thread with chem summary
- Post `@PaperBrain summarize for elia 10.1021/xxx` → get ML summary in thread

**Obsidian:**
- All papers land in `PaperBrain/Papers/` with frontmatter + both summaries
- Tagged with `inbox` until you process them
- Relevance score 1-5 in frontmatter for filtering

---

## Troubleshooting

**Bot not responding in channels:**
- Check the channel IDs in config match the actual channels
- Make sure the bot is invited to those channels: `/invite @PaperBrain`

**Ollama errors:**
- Check ollama is running: `ollama list`
- Check it responds: `curl http://localhost:11434/api/tags`
- qwen3:8b loaded: `ollama pull qwen3:8b`

**Obsidian notes not appearing:**
- iCloud sync can be slow — wait a minute
- Check the vault path in config is exactly right
- Check file permissions on the vault folder

**PDF not processing:**
- Some paywalled PDFs won't have extractable text (scanned images)
- PyMuPDF handles most PDFs but not image-only scans
