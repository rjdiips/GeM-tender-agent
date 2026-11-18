# Index timers (user units)

    cp deploy/systemd/gem-index-*.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now gem-index-sync.timer gem-index-refresh.timer
    loginctl enable-linger $USER   # keep timers running when logged out

Both jobs take the same lock (`data/index/sync.lock`); a sync that finds the nightly refresh running skips its turn.
