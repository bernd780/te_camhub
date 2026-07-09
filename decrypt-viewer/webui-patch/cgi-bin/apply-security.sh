#!/bin/bash
# Applies the security toggles from teslausb_setup_variables.conf:
#   SSH_DISABLE_PASSWORD, WEB_AUTH, WEB_TLS
# - sshd: PasswordAuthentication no (drop-in) + reload
# - nginx: regenerate the site with optional basic-auth + optional self-signed TLS
# Regenerating (instead of sed) keeps it idempotent. nginx -t gates every reload
# so a bad config can never lock the user out.
#
# Invoked with HTTP CGI headers OR standalone. Prints a JSON status line last.

CONF=/root/teslausb_setup_variables.conf
SITE=/etc/nginx/sites-available/teslausb.nginx
TLSDIR=/mutable/tls
CERT=$TLSDIR/cert.pem
KEY=$TLSDIR/key.pem

getval() {
  local line; line=$(sudo grep -m1 "^export $1=" "$CONF" 2>/dev/null); line=${line#export $1=}
  if [[ "$line" == \'*\' ]]; then line=${line#\'}; line=${line%\'}
  elif [[ "$line" == \"*\" ]]; then line=${line#\"}; line=${line%\"}; fi
  printf '%s' "$line"
}

SSH_OFF=$(getval SSH_DISABLE_PASSWORD)

# HARD DISABLE of nginx-based web-auth/TLS on this image. Two independent
# blockers, both verified live:
#   (a) this nginx build has no http_ssl_module -> a `listen 443 ssl` block makes
#       nginx fail to start (port 80 would be dead after reboot);
#   (b) nginx config *reloads are no-ops* here (a /var/log/nginx log-permission
#       quirk), so auth/TLS changes never take effect without a risky restart.
# Until both are fixed, we NEVER emit auth_basic or an SSL block -> impossible to
# lock anyone out. The viewer (:8099) is already protected by the vault passphrase.
WEB_AUTH=false
WEB_TLS=false
TLS_SUPPORTED=false
TLS_NOTE=' (Web-Login/TLS auf diesem Image nicht verfügbar)'

result='{"ok":true}'

sudo /root/bin/remountfs_rw >/dev/null 2>&1

# ---- SSH ---------------------------------------------------------------------
SSHD=/etc/ssh/sshd_config.d/99-teslausb.conf
if [ "$SSH_OFF" = "true" ]; then
  echo "PasswordAuthentication no" | sudo tee "$SSHD" >/dev/null
else
  sudo rm -f "$SSHD"
fi
sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd 2>/dev/null || true

# ---- TLS cert (self-signed, once) --------------------------------------------
if [ "$WEB_TLS" = "true" ] && [ ! -f "$CERT" ]; then
  sudo mkdir -p "$TLSDIR"
  sudo openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
       -keyout "$KEY" -out "$CERT" -subj "/CN=teslausb" >/dev/null 2>&1
  sudo chmod 600 "$KEY"
fi

# ---- nginx site regeneration -------------------------------------------------
# Lockout guard: only actually enable basic-auth if a non-empty .htpasswd
# exists (it is written by the viewer on vault unlock). Otherwise keep auth off
# so enabling WEB_AUTH before the first unlock can never lock the user out.
if [ "$WEB_AUTH" = "true" ] && [ -s /etc/nginx/.htpasswd ]; then
  AUTH='auth_basic "TeslaUSB"; auth_basic_user_file /etc/nginx/.htpasswd;'
else
  AUTH='auth_basic off;'
fi

# the reusable body (root + the three teslausb locations)
read -r -d '' BODY <<EOF
    root /var/www/html;
    index index.html;
    server_name _;
    client_max_body_size 0;
    fastcgi_request_buffering off;

    location / {
        try_files \\\$uri \\\$uri/ =404;
        $AUTH
    }
    location /cgi-bin/ {
        $AUTH
        gzip off;
        root /var/www/html;
        fastcgi_pass  unix:/var/run/fcgiwrap.socket;
        include /etc/nginx/fastcgi_params;
        fastcgi_param SCRIPT_FILENAME  \\\$document_root\\\$fastcgi_script_name;
        fastcgi_max_temp_file_size 0;
    }
    location /TeslaCam/ {
        $AUTH
        root /var/www/html;
        fancyindex on;
        fancyindex_css_href /fancyindex.css;
    }
EOF

TMP=$(mktemp)
if [ "$WEB_TLS" = "true" ]; then
  cat > "$TMP" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 301 https://\$host\$request_uri;
}
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
$BODY
}
EOF
else
  cat > "$TMP" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
$BODY
}
EOF
fi

sudo cp "$SITE" "$SITE.prev" 2>/dev/null
sudo cp "$TMP" "$SITE"
rm -f "$TMP"

# NB: `nginx -t` exit code is unreliable here — the tmpfs log mounts make it
# emit a spurious "[emerg] open() .../error.log (13)" and return non-zero even
# for a perfectly valid config. It still prints "syntax is ok" iff the config
# actually parses, so gate on that. (systemctl reload itself works fine because
# the running master already holds the log fds.)
syntax_ok() { sudo nginx -t 2>&1 | grep -q "syntax is ok"; }

if syntax_ok; then
  sudo systemctl reload nginx
  result="{\"ok\":true,\"tls_supported\":$TLS_SUPPORTED${TLS_NOTE:+,\"note\":\"${TLS_NOTE}\"}}"
else
  # revert to previous known-good config; never leave nginx broken
  sudo cp "$SITE.prev" "$SITE" 2>/dev/null
  sudo systemctl reload nginx 2>/dev/null
  result='{"ok":false,"error":"nginx config test failed, reverted"}'
fi

sudo mount / -o remount,ro 2>/dev/null

printf 'HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n%s\n' "$result"
