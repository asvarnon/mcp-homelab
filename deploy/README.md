# Deployment (systemd)

This directory contains the systemd unit used to run mcp-homelab as a managed service in the LXC container.

## Install

cp deploy/mcp-homelab.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now mcp-homelab

## Common commands

systemctl status mcp-homelab
systemctl start mcp-homelab
systemctl stop mcp-homelab
systemctl restart mcp-homelab
journalctl -u mcp-homelab -f

## Automation note

A deployment script (deploy.py) is planned to automate these steps.
