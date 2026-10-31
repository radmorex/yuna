"""Discord-адаптер «Юны».

Запуск из корня проекта:  python -m discord_bot.bot
Импорт модуля ничего не подключает — всё стартует в main().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from . import logic

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("mira.discord")

BOT_NAMES = logic.DEFAULT_BOT_NAMES


class _RedactFilter(logging.Filter):
    """Страховка: вырезает секреты из любых строк логов (в т.ч. от discord.py/aiohttp)."""

    def __init__(self, secrets):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record):
        if self.secrets:
            msg = record.getMessage()
            red = msg
            for s in self.secrets:
                red = red.replace(s, logic.mask_token(s))
            red = logic.mask_url_token(red)
            if red != msg:
                record.msg, record.args = red, ()
        return True


def build_client(cfg: logic.BotConfig):
    """Создаёт discord.Client. Импорт discord/aiohttp — только здесь (ленивый)."""
    import aiohttp
    import discord

    intents = discord.Intents.default()
    intents.message_content = True  # нужно включить в Developer Portal!
    intents.guilds = True
    intents.voice_states = cfg.voice_enabled

    no_pings = discord.AllowedMentions.none()

    class MiraClient(discord.Client):
        def __init__(self):
            super().__init__(intents=intents, allowed_mentions=no_pings)
            self.cfg = cfg
            self.limiter = logic.RateLimiter(max_events=3, window=30.0)
            self.out_q: asyncio.Queue = asyncio.Queue(maxsize=200)
            self.audio_q: asyncio.Queue = asyncio.Queue(maxsize=50)
            self.http_session: aiohttp.ClientSession | None = None
            self.voice: discord.VoiceClient | None = None
            self._bg: list[asyncio.Task] = []

        async def setup_hook(self):
            self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
            self._bg.append(asyncio.create_task(self.bridge_loop(), name="bridge"))
            if self.cfg.voice_enabled:
                self._bg.append(asyncio.create_task(self.voice_loop(), name="voice"))

        async def close(self):
            for t in self._bg:
                t.cancel()
            if self.voice and self.voice.is_connected():
                await self.voice.disconnect(force=True)
            if self.http_session:
                await self.http_session.close()
            await super().close()

        # ---------- Discord events ----------
        async def on_ready(self):
            log.info("Вошла как %s (id=%s). Конфиг: %r", self.user, self.user.id, self.cfg)
            if self.cfg.voice_enabled and self.cfg.voice_channel_id:
                await self.join_voice()
            elif self.cfg.voice_enabled:
                log.warning("DISCORD_VOICE_ENABLED=1, но DISCORD_VOICE_CHANNEL_ID не задан — голос выключен")

        async def join_voice(self):
            ch = self.get_channel(int(self.cfg.voice_channel_id))
            if ch is None or not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                log.error("Голосовой канал %s не найден или это не voice-канал", self.cfg.voice_channel_id)
                return
            try:
                if self.voice and self.voice.is_connected():
                    await self.voice.move_to(ch)
                else:
                    self.voice = await ch.connect(reconnect=True, self_deaf=True)
                log.info("Подключилась к голосовому каналу %s", ch.name)
            except Exception as e:  # noqa: BLE001
                log.error("Не удалось зайти в голосовой канал: %s", e)

        async def on_message(self, message: discord.Message):
            if message.author.bot or message.guild is None:
                return
            if not self.cfg.channel_allowed(message.channel.id):
                return
            mentioned = self.user is not None and self.user in message.mentions
            if not logic.should_respond(message.content, mentioned, BOT_NAMES):
                return
            if not self.limiter.allow(message.author.id):
                log.info("rate-limit: %s", message.author.id)
                return
            text = logic.strip_trigger(message.content, BOT_NAMES)
            if not text:
                return
            role_ids = [r.id for r in getattr(message.author, "roles", [])]
            ev = logic.build_bridge_event(
                user=message.author.display_name,
                text=text,
                is_admin=logic.is_admin(message.author.id, role_ids,
                                        self.cfg.admin_user_ids, self.cfg.admin_role_ids),
                channel_id=message.channel.id,
                message_id=message.id,
                mentioned=mentioned,
                user_id=message.author.id,
            )
            try:
                self.out_q.put_nowait(ev)
            except asyncio.QueueFull:
                log.warning("Очередь в ядро переполнена — сообщение отброшено")

        # ---------- Bridge ----------
        async def bridge_loop(self):
            url = logic.bridge_ws_url(self.cfg.core_url, self.cfg.bridge_token)
            safe = logic.mask_url_token(url)
            delays = logic.backoff_delays(1.0, 30.0)
            while not self.is_closed():
                try:
                    async with self.http_session.ws_connect(url, heartbeat=20, max_msg_size=1 << 20) as ws:
                        log.info("Bridge подключён: %s", safe)
                        delays = logic.backoff_delays(1.0, 30.0)
                        sender = asyncio.create_task(self._bridge_sender(ws))
                        try:
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    await self.handle_core_message(msg.data)
                                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                    break
                        finally:
                            sender.cancel()
                    log.warning("Bridge закрыт ядром")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("Bridge недоступен (%s): %s", safe, type(e).__name__)
                await asyncio.sleep(next(delays))

        async def _bridge_sender(self, ws):
            while True:
                ev = await self.out_q.get()
                try:
                    await ws.send_str(json.dumps(ev, ensure_ascii=False))
                except Exception:
                    # вернуть в очередь, чтобы не потерять при переподключении
                    try:
                        self.out_q.put_nowait(ev)
                    except asyncio.QueueFull:
                        pass
                    raise

        async def handle_core_message(self, raw: str):
            try:
                rep = logic.parse_bridge_message(raw)
            except logic.BridgeMessageError as e:
                log.warning("Некорректное сообщение от ядра: %s", e)
                return
            ch = self.get_channel(int(rep.channel_id))
            if ch is None or not self.cfg.channel_allowed(rep.channel_id):
                log.warning("Канал %s недоступен/не разрешён", rep.channel_id)
                return
            chunks = logic.chunk_text(rep.text)
            ref = None
            if rep.reply_to_message_id:
                ref = discord.MessageReference(message_id=int(rep.reply_to_message_id),
                                               channel_id=int(rep.channel_id), fail_if_not_exists=False)
            try:
                for i, part in enumerate(chunks):
                    await ch.send(part, reference=ref if i == 0 else None, allowed_mentions=no_pings)
            except discord.HTTPException as e:
                log.error("Не удалось отправить ответ: %s", e)
            if self.cfg.voice_enabled and rep.audio_url:
                full = logic.resolve_audio_url(self.cfg.core_url, rep.audio_url)
                if full is None:
                    log.warning("audioUrl отклонён (не с хоста ядра): %s", rep.audio_url)
                else:
                    try:
                        self.audio_q.put_nowait(full)
                    except asyncio.QueueFull:
                        log.warning("Очередь озвучки переполнена — клип пропущен")

        # ---------- Voice ----------
        async def voice_loop(self):
            while True:
                url = await self.audio_q.get()
                path = None
                try:
                    if not (self.voice and self.voice.is_connected()):
                        continue
                    async with self.http_session.get(url) as resp:
                        if resp.status != 200:
                            log.warning("Аудио %s → HTTP %s", url, resp.status)
                            continue
                        data = await resp.read()
                    if len(data) > 20 * 1024 * 1024:
                        log.warning("Аудио слишком большое, пропуск")
                        continue
                    fd, path = tempfile.mkstemp(prefix="mira_", suffix=".wav")
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    done = asyncio.Event()
                    loop = asyncio.get_running_loop()
                    src = discord.FFmpegPCMAudio(path, executable=_ffmpeg_path())
                    self.voice.play(src, after=lambda _e: loop.call_soon_threadsafe(done.set))
                    await asyncio.wait_for(done.wait(), timeout=300)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.error("Ошибка воспроизведения: %s", e)
                finally:
                    if path:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass

    return MiraClient()


def _ffmpeg_path() -> str:
    import shutil
    return os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logic.load_env_file(str(ROOT / ".env"))
    cfg = logic.BotConfig.from_env(os.environ)
    global BOT_NAMES
    BOT_NAMES = logic.bot_names_from_env(os.environ)
    if not cfg.token:
        print("DISCORD_TOKEN не задан — Discord-бот не запущен. "
              "Заполни DISCORD_TOKEN в .env (см. discord_bot/DISCORD.md).")
        return 0
    redact = _RedactFilter([cfg.token, cfg.bridge_token])
    for h in logging.getLogger().handlers:
        h.addFilter(redact)
    if not cfg.bridge_token:
        log.warning("BRIDGE_TOKEN пуст — ядро, скорее всего, отклонит подключение")
    try:
        import discord  # noqa: F401
    except ImportError:
        print("Не установлен discord.py. Выполни: uv pip install -r discord_bot/requirements.txt")
        return 1
    client = build_client(cfg)
    client.run(cfg.token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
