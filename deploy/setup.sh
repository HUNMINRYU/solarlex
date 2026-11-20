#!/usr/bin/env bash
# Oracle Cloud(OCI) Ubuntu 24.04 인스턴스에서 1회 실행합니다. 사용자는 기본 계정 ubuntu 기준입니다.
# 사용법: SOLARLEX_DOMAIN=<공인IP>.sslip.io bash deploy/setup.sh
#
# 실행 전 OCI 콘솔에서 할 일: 인스턴스의 서브넷 Security List(또는 NSG)에
#   Ingress 규칙 2개 추가 — Source 0.0.0.0/0, TCP, 목적지 포트 80 / 443
set -euo pipefail

: "${SOLARLEX_DOMAIN:?SOLARLEX_DOMAIN(예: api.example.com 또는 <공인IP>.sslip.io)을 지정하세요}"
APP=/home/ubuntu/solarlex

sudo apt-get update -y
sudo apt-get install -y python3.12-venv caddy

# OCI Ubuntu 이미지는 호스트 iptables가 SSH(22)만 허용합니다. 80/443을 지금 바로 열고(REJECT 규칙 앞),
# 재부팅 후에도 남도록 /etc/iptables/rules.v4의 REJECT 줄 앞에 같은 규칙을 넣습니다.
# 'netfilter-persistent save'는 Tailscale·Docker가 만든 동적 규칙까지 저장해 버려 쓰지 않습니다.
REJECT_LINE=$(sudo iptables -S INPUT | grep -n -- '-j REJECT' | head -1 | cut -d: -f1)
for port in 80 443; do
  RULE="-p tcp -m state --state NEW -m tcp --dport $port -j ACCEPT"
  sudo iptables -C INPUT $RULE 2>/dev/null || sudo iptables -I INPUT "$((REJECT_LINE - 1))" $RULE
  if [ -f /etc/iptables/rules.v4 ] && ! sudo grep -q -- "--dport $port -j ACCEPT" /etc/iptables/rules.v4; then
    sudo sed -i "0,/^-A INPUT -j REJECT/s//-A INPUT $RULE\n&/" /etc/iptables/rules.v4
  fi
done

cd "$APP/engine"
python3.12 -m venv .venv
.venv/bin/pip install -q -r requirements.txt   # rhwp-python은 별표 재변환에만 필요해 서버엔 설치하지 않음(ARM 휠 미확인)

test -f .env || { echo ".env가 없습니다. UPSTAGE_API_KEY, LAW_OC를 넣은 engine/.env를 먼저 올리세요."; exit 1; }
chmod 600 .env
# 벡터 DB·지식그래프·예상 질문 색인(data/solarlex.db). 주제어·예상 질문은 저장소의 JSON을 재사용하고 임베딩만 다시 부릅니다.
test -f data/solarlex.db || { .venv/bin/python -X utf8 build_index.py && .venv/bin/python -X utf8 learn.py; }

sudo cp "$APP/deploy/solarlex.service" /etc/systemd/system/solarlex.service
sudo systemctl daemon-reload
sudo systemctl enable --now solarlex

# Caddy가 도메인으로 HTTPS 인증서를 받아 127.0.0.1:8010으로 넘깁니다(8000은 같은 서버의 Docker용으로 비워 둠).
sed "s/{\$SOLARLEX_DOMAIN}/$SOLARLEX_DOMAIN/" "$APP/deploy/Caddyfile" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart caddy

sleep 3
curl -fsS http://127.0.0.1:8010/health && echo
echo "배포 완료: https://$SOLARLEX_DOMAIN/ask (POST), 시연 화면 https://$SOLARLEX_DOMAIN/"
