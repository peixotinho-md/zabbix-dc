#!/usr/bin/env python3
"""
Bot do Discord do NOC - Atividade 12 "Sinais Vitais".

O Zabbix chama este bot por um media type do tipo Webhook (POST http://discord-bot:8081/zabbix).
O bot publica o alerta como embed no canal do NOC e, quando o problema é resolvido,
responde na mesma mensagem e marca o alerta original como resolvido.

Variáveis (vêm do discord.env, criado por ./lab.sh discord):
  DISCORD_TOKEN       token do bot (Discord Developer Portal > Bot > Reset Token)
  DISCORD_CHANNEL_ID  ID do canal do NOC (modo desenvolvedor > botão direito no canal > Copiar ID)
  DISCORD_ROLE_ID     opcional: cargo marcado (@) nos alertas Disaster
  BOT_SECRET          segredo compartilhado com o Zabbix (cabeçalho X-Bot-Secret)
  ZABBIX_URL          link mostrado no alerta (padrão http://localhost:8080)
"""
import asyncio, json, logging, os
from datetime import datetime

import discord
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot-noc")

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
CANAL = int(os.environ.get("DISCORD_CHANNEL_ID", "0").strip() or 0)
CARGO = os.environ.get("DISCORD_ROLE_ID", "").strip()
SEGREDO = os.environ.get("BOT_SECRET", "")
ZABBIX_URL = os.environ.get("ZABBIX_URL", "http://localhost:8080").rstrip("/")
PORTA = int(os.environ.get("BOT_PORT", "8081"))
ESTADO = os.environ.get("BOT_ESTADO", "/data/eventos.json")   # event_id do Zabbix -> id da mensagem

# Cores e rótulos por severidade do Zabbix (0 a 5)
SEVERIDADES = {
    0: ("Não classificado", 0x97AAB3, "⚪"),
    1: ("Informação", 0x7499FF, "🔵"),
    2: ("Warning", 0xFFC859, "🟡"),
    3: ("Average", 0xFFA059, "🟠"),
    4: ("High", 0xE97659, "🔴"),
    5: ("Disaster", 0xE45959, "🚨"),
}
VERDE, CINZA = 0x59DB8F, 0x6E7681

intents = discord.Intents.default()   # o bot só envia mensagens: nenhum intent privilegiado
cliente = discord.Client(intents=intents)


def limpo(v):
    """Macro do Zabbix não resolvida chega literal ("{EVENT.RECOVERY.DATE}"): trata como vazio."""
    v = (v or "").strip()
    return "" if v.startswith("{") and v.endswith("}") else v


def carregar_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def salvar_estado(estado):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    if len(estado) > 500:   # guarda só os 500 eventos mais recentes
        estado = dict(list(estado.items())[-500:])
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f)
    os.replace(tmp, ESTADO)


def embed_problema(a):
    nsev = int(limpo(a.get("nseverity")) or 0)
    rotulo, cor, icone = SEVERIDADES.get(nsev, SEVERIDADES[0])
    e = discord.Embed(title=f"{icone} {rotulo.upper()}: {limpo(a.get('name'))}", color=cor,
                      url=f"{ZABBIX_URL}/zabbix.php?action=problem.view")
    e.add_field(name="Equipamento", value=f"{limpo(a.get('host')) or '-'} ({limpo(a.get('ip')) or '-'})", inline=False)
    e.add_field(name="Valor atual", value=limpo(a.get("value")) or "-", inline=True)
    e.add_field(name="Severidade", value=rotulo, inline=True)
    e.add_field(name="Início", value=f"{limpo(a.get('date'))} {limpo(a.get('time'))}".strip() or "-", inline=True)
    e.add_field(name="Ação sugerida",
                value="Verificar sessões BGP, limitar prefixos (maximum-prefix) e conferir processos no roteador.",
                inline=False)
    e.set_footer(text=f"Zabbix • evento {limpo(a.get('event_id'))}")
    return e


def embed_resolvido(a):
    e = discord.Embed(title=f"✅ RESOLVIDO: {limpo(a.get('name'))}", color=VERDE)
    e.add_field(name="Equipamento", value=limpo(a.get("host")) or "-", inline=True)
    e.add_field(name="Duração", value=limpo(a.get("duration")) or "-", inline=True)
    e.add_field(name="Resolvido em",
                value=f"{limpo(a.get('recovery_date'))} {limpo(a.get('recovery_time'))}".strip() or "-", inline=True)
    e.add_field(name="Valor atual", value=limpo(a.get("value")) or "-", inline=True)
    e.set_footer(text=f"Zabbix • evento {limpo(a.get('event_id'))}")
    return e


async def publicar(a):
    canal = cliente.get_channel(CANAL) or await cliente.fetch_channel(CANAL)
    estado = carregar_estado()
    evento = limpo(a.get("event_id"))
    recuperacao = limpo(a.get("event_value")) == "0"
    atualizacao = limpo(a.get("event_update")) == "1"

    if atualizacao:   # ack/comentário no Zabbix: só registra, não polui o canal
        log.info("evento %s: atualização ignorada", evento)
        return "atualizacao ignorada"

    if not recuperacao:
        mencao = f"<@&{CARGO}> " if CARGO and limpo(a.get("nseverity")) == "5" else ""
        msg = await canal.send(content=mencao + "Alerta do NOC" if mencao else None, embed=embed_problema(a),
                               allowed_mentions=discord.AllowedMentions(roles=True))
        estado[evento] = msg.id
        salvar_estado(estado)
        log.info("evento %s: problema publicado (mensagem %s)", evento, msg.id)
        return "problema publicado"

    # Recuperação: responde no alerta original e deixa ele cinza com "resolvido" no título
    original = None
    if evento in estado:
        try:
            original = await canal.fetch_message(estado[evento])
            antigo = original.embeds[0] if original.embeds else None
            if antigo:
                antigo.color = CINZA
                antigo.title = "~~" + (antigo.title or "") + "~~  (resolvido)"
                await original.edit(embed=antigo)
        except discord.NotFound:
            original = None
    await canal.send(embed=embed_resolvido(a), reference=original, mention_author=False)
    estado.pop(evento, None)
    salvar_estado(estado)
    log.info("evento %s: recuperação publicada", evento)
    return "recuperacao publicada"


async def rota_zabbix(req):
    if SEGREDO and req.headers.get("X-Bot-Secret") != SEGREDO:
        return web.json_response({"erro": "segredo invalido"}, status=403)
    if not TOKEN or not CANAL:
        return web.json_response({"erro": "bot sem DISCORD_TOKEN/DISCORD_CHANNEL_ID: rode ./lab.sh discord"},
                                 status=503)
    if not cliente.is_ready():
        return web.json_response({"erro": "bot ainda conectando ao Discord"}, status=503)
    try:
        alerta = await req.json()
    except json.JSONDecodeError:
        return web.json_response({"erro": "JSON invalido"}, status=400)
    try:
        resultado = await publicar(alerta)
    except discord.Forbidden:
        return web.json_response({"erro": "sem permissao no canal (Ver canal / Enviar mensagens / Inserir links / Ver historico)"},
                                 status=502)
    except discord.HTTPException as e:
        return web.json_response({"erro": f"Discord: {e.status} {e.text}"}, status=502)
    return web.json_response({"ok": resultado})


async def rota_saude(_req):
    return web.json_response({
        "token_configurado": bool(TOKEN), "canal": CANAL or None,
        "conectado": cliente.is_ready(), "usuario": str(cliente.user) if cliente.user else None,
        "agora": datetime.now().isoformat(timespec="seconds"),
    })


@cliente.event
async def on_ready():
    log.info("conectado ao Discord como %s; canal do NOC: %s", cliente.user, CANAL)


async def main():
    app = web.Application()
    app.add_routes([web.post("/zabbix", rota_zabbix), web.get("/saude", rota_saude)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORTA).start()
    log.info("recebendo alertas do Zabbix em http://0.0.0.0:%s/zabbix", PORTA)
    if not TOKEN or not CANAL:
        log.warning("DISCORD_TOKEN/DISCORD_CHANNEL_ID vazios: rode ./lab.sh discord (ou 8-DISCORD.bat). "
                    "Até lá o Zabbix recebe 503 e o envio aparece como falha no Action log.")
        await asyncio.Event().wait()
    try:
        async with cliente:
            await cliente.start(TOKEN)
    except discord.LoginFailure:
        log.error("TOKEN RECUSADO PELO DISCORD (401). Use o token da aba Bot > Reset Token, "
                  "nao o Application ID, a Public Key nem o Client Secret. Depois: ./lab.sh discord")
        await asyncio.Event().wait()   # fica no ar respondendo 503 ao Zabbix, sem reiniciar em loop


if __name__ == "__main__":
    asyncio.run(main())
