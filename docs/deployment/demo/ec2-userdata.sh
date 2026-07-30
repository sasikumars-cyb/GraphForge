#!/usr/bin/env bash
# EC2 launch "User data" script - paste this into the console's User data
# field when launching the instance (Amazon Linux 2023 AMI assumed). Runs
# once, automatically, on first boot: installs Docker + the Compose plugin,
# and adds a swap file. A t3.micro/t4g.micro only has 1GB RAM; Postgres +
# backend + frontend + Caddy together fit, but with little headroom - the
# swap file is a cheap (EBS-backed, ~$0.08/GB/month within free tier)
# safety net against an OOM kill under a brief traffic spike.
set -euo pipefail

dnf update -y
dnf install -y docker git

systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user

# Docker Compose v2 plugin (Amazon Linux 2023's docker package doesn't
# bundle it).
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 1GB swap file - only matters under memory pressure, otherwise unused.
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo "/swapfile none swap sw 0 0" >> /etc/fstab
