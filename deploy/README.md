# Dashboard — systemd unit (Hetzner)

Sluša SAMO na localhost. Javni pristup ide kroz Cloudflare Tunnel ili Tailscale,
nikad kroz otvoren port na vatrozidu.

    sudo cp /opt/zarko/deploy/dashboard.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now dashboard

Unit se zove `dashboard` (po imenu datoteke). Ako dashboard vec vrti kao obican
`nohup` proces, prvo ga ubij (`pkill -f 'view.py serve'`) — inace port 8787
ostane zauzet i servis se ne digne.

`deploy.sh` restarta taj unit sam. Restart je obavezan: HTML se renderira iz
koda u memoriji procesa, a CSS (`web/static/`) se cita s diska po zahtjevu —
bez restarta dobijes novi CSS na starom HTML-u.

Provjera da nije izložen:

    ss -tlnp | grep 8787
    # mora biti 127.0.0.1:8787, ne 0.0.0.0:8787
