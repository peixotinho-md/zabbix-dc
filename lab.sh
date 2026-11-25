#!/bin/bash
# Atividade 12 - "Sinais Vitais": atalhos do lab (Linux, macOS, WSL ou GitHub Codespaces).
# Só precisa de Docker com o plugin "docker compose". Uso: ./lab.sh <comando>
set -e
cd "$(dirname "$0")"

uso() {
cat <<'TXT'
Uso: ./lab.sh <comando>

  iniciar      sobe os 7 containers e configura o Zabbix sozinho (1a vez: 3 a 6 min)
  status       valores atuais, problemas ativos e e-mails enviados (via API do Zabbix)
  painel       status atualizado a cada 15 s (Ctrl+C sai)
  testes       sessao BGP + 4 testes de seguranca do SNMPv3
  demo [min]   ISP manda +10.000 rotas e a CPU do roteador vai a 100% (padrao 8 min)
  parar-demo   para o estresse e retira as rotas extras
  email        configura o envio REAL de e-mail (Gmail + senha de app) - opcional
  discord      configura o bot do Discord (token + ID do canal do NOC) - opcional
  discord-teste  manda um Disaster de teste + resolucao para o canal do Discord
  desligar     para os containers (configuracao e historico preservados)
  apagar       remove tudo, inclusive o banco do Zabbix (volta do zero)
TXT
}

compose() { docker compose "$@"; }
prefixos() { docker exec roteador-borda bgp-rotas 2>/dev/null || echo 0; }
status_py() { compose run --rm --no-deps configurador python scripts/status.py; }

esperar_docker() {
  for i in $(seq 1 60); do docker info >/dev/null 2>&1 && return 0; sleep 2; done
  echo "ERRO: o Docker nao esta rodando. Abra o Docker (Docker Desktop no Windows/macOS) e tente de novo."; exit 1
}

case "${1:-}" in
  iniciar)
    esperar_docker
    echo ">> 1/3 Subindo os containers (a 1a vez baixa ~600 MB e constroi a imagem do roteador)"
    compose up -d --build
    echo; echo ">> 2/3 Configurando o Zabbix pela API (host, itens, triggers, NOC, action)"
    docker logs -f zbx-configurador || true
    if [ "$(docker wait zbx-configurador)" != "0" ]; then
      echo "ERRO: a configuracao do Zabbix falhou (veja acima). Rode ./lab.sh iniciar de novo."; exit 1
    fi
    echo; echo ">> 3/3 Conferindo o BGP"
    for i in $(seq 1 45); do P=$(prefixos); [ "${P:-0}" -gt 0 ] 2>/dev/null && break; sleep 2; done
    echo "   Prefixos BGP recebidos do ISP: $P"
    echo
    echo "============  LAB PRONTO: http://localhost:8080   (Admin / zabbix)  ============"
    [ -n "$CODESPACES" ] && echo "   (Codespaces: abra a aba PORTS e clique no globo da porta 8080)"
    echo "   Proximos passos: ./lab.sh testes  |  ./lab.sh demo  |  ./lab.sh painel"
    ;;
  status) status_py ;;
  painel)
    while true; do
      clear
      echo "=========  LAB ATIVIDADE 12 - SINAIS VITAIS  |  $(date +%H:%M:%S)  |  Ctrl+C para sair  ========="
      status_py 2>&1 || echo "(Zabbix ainda subindo...)"
      sleep 15
    done ;;
  testes) docker exec roteador-borda bash /scripts/testes.sh ;;
  demo)
    MIN="${2:-8}"
    echo ">> ISP anunciando +10.000 rotas para o roteador de borda..."
    docker exec isp-transito bash /scripts/rotas.sh anunciar
    sleep 5; echo "   Prefixos recebidos agora: $(prefixos)"
    echo ">> Estressando TODOS os nucleos do roteador a 100% por $MIN minutos (stress-ng)..."
    docker exec -d roteador-borda stress-ng --cpu 0 --cpu-load 100 --timeout "${MIN}m"
    echo "   Inicio: $(date +%H:%M:%S). A media de 5 min passa de 90% em ~6 min -> trigger DISASTER -> e-mail ao NOC."
    echo "   Acompanhe: ./lab.sh painel  ou  http://localhost:8080 > Monitoring > Problems"
    ;;
  parar-demo)
    docker exec roteador-borda pkill stress-ng 2>/dev/null && echo ">> stress-ng parado" || echo ">> stress-ng nao estava rodando"
    docker exec isp-transito bash /scripts/rotas.sh retirar
    sleep 3; echo ">> Rotas extras retiradas. Prefixos agora: $(prefixos)"
    ;;
  email)
    read -rp "Gmail que ENVIA o alerta (remetente): " G
    read -rp "E-mail do NOC que RECEBE o alerta: " N
    compose run --rm --no-deps configurador python scripts/configurar_email.py --gmail "$G" --noc "$N"
    echo "Senha de app do Gmail (16 letras, criada em https://myaccount.google.com/apppasswords):"
    compose run --rm --no-deps -it configurador python scripts/configurar_senha.py
    ;;
  discord)
    echo "Bot do Discord do NOC. Precisa de: token do bot e ID do canal (passo a passo no README)."
    read -rsp "Token do bot (nao aparece na tela): " T; echo
    T=$(printf '%s' "$T" | tr -d ' "\r\n')
    if [ "$(printf '%s' "$T" | tr -cd '.' | wc -c)" -ne 2 ] || [ ${#T} -lt 50 ]; then
      echo "ERRO: isso nao parece o token do bot (ele tem ~70 caracteres e 2 pontos, tipo MTIz...Gx1a2b.abc...)."
      echo "Pegue na aba Bot > Reset Token. Application ID, Public Key e Client Secret NAO servem."; exit 1
    fi
    read -rp "ID do canal do NOC: " C
    C=$(printf '%s' "$C" | tr -cd '0-9')
    read -rp "ID do cargo a marcar no Disaster (Enter = nenhum): " R
    umask 077
    printf 'DISCORD_TOKEN=%s\nDISCORD_CHANNEL_ID=%s\nDISCORD_ROLE_ID=%s\n' "$T" "$C" "$R" > discord.env
    echo ">> discord.env gravado (fica so no seu PC, esta no .gitignore). Reiniciando o bot..."
    compose up -d --build --force-recreate discord-bot
    for i in $(seq 1 20); do
      S=$(docker exec discord-bot python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8081/saude').read().decode())" 2>/dev/null || true)
      echo "$S" | grep -q '"conectado": true' && break; sleep 2
    done
    echo "   $S"
    echo "$S" | grep -q '"conectado": true' && echo ">> Bot online. Teste: ./lab.sh discord-teste" \
      || echo "ERRO: o bot nao conectou. Veja: docker logs discord-bot"
    ;;
  discord-teste) docker exec discord-bot python teste.py "${2:-10}" ;;
  desligar) compose stop ;;
  apagar) compose down -v ;;
  *) uso ;;
esac
