Systemd Service for PingX Bot

Files
- pingx-bot.service — sample unit file you can copy to systemd.

Assumptions in the unit file
- Project directory: /opt/pingx-bot
- Virtualenv:       /opt/pingx-bot/.venv
- .env file:        /opt/pingx-bot/.env
- User/Group:       pingx:pingx

You can change these paths to match your server.

Setup (Ubuntu/Debian/RHEL)
1) Copy files
   sudo mkdir -p /opt/pingx-bot
   # rsync or git clone your repo into /opt/pingx-bot
   # ensure .env and .venv exist (python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt)

2) Edit unit file to match your paths
   sudo cp deploy/pingx-bot.service /etc/systemd/system/pingx-bot.service
   sudo nano /etc/systemd/system/pingx-bot.service

3) Reload and enable
   sudo systemctl daemon-reload
   sudo systemctl enable --now pingx-bot

4) Logs and control
   sudo systemctl status pingx-bot
   sudo journalctl -u pingx-bot -f
   sudo systemctl restart pingx-bot

Notes
- Environment variables are read from /opt/pingx-bot/.env. Keep TELEGRAM_BOT_TOKEN and other secrets there.
- If you use a different python path or a process manager like uvloop, update ExecStart accordingly.
- On SELinux or hardened systems you may need to relax ProtectSystem/ProtectHome.

