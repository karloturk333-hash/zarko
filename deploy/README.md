# Dashboard — systemd unit (Hetzner)

Sluša SAMO na localhost. Javni pristup ide kroz Cloudflare Tunnel ili Tailscale,
nikad kroz otvoren port na vatrozidu.

    sudo cp /opt/zarko/deploy/dashboard.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now zarko-dashboard

Provjera da nije izložen:

    ss -tlnp | grep 8787
    # mora biti 127.0.0.1:8787, ne 0.0.0.0:8787
