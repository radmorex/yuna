"""Логика слова-активатора без зависимостей: нарезка речи на фразы и поиск «Юна» в начале фразы."""
import array
import math
import re

RATE = 16_000
FRAME_BYTES = 960  # 30 мс, 16 бит, моно
PREROLL_FRAMES = 10
MIN_VOICED_FRAMES = 5  # 0,15 с: короче щелчок, а голое «Юна» длится меньше полсекунды
END_SILENCE_FRAMES = 23  # 0,7 с тишины заканчивают фразу
MAX_PHRASE_FRAMES = 400  # 12 с: длиннее не команда, а музыка или чужой разговор
MIN_LEVEL = 0.008
WAKE_WORDS = ("юна", "юно", "юню", "юнна", "йуна", "уна", "діуна", "yuna")


def rms(frame):
    samples = array.array("h", frame[: len(frame) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0


class Segmenter:
    """Копит кадры и отдаёт целую фразу, когда после речи наступает тишина. Порог подстраивается под шум комнаты."""

    def __init__(self):
        self.floor = MIN_LEVEL / 3
        self.reset()

    def reset(self):
        self.before = []
        self.frames = []
        self.voiced = 0
        self.silent = 0

    def feed(self, frame):
        level = rms(frame)
        loud = level > max(MIN_LEVEL, self.floor * 2.5)
        if not loud:
            self.floor += (level - self.floor) * 0.02
        if not self.frames:
            self.before = (self.before + [frame])[-PREROLL_FRAMES:]
            if loud:
                self.frames, self.before = self.before, []
                self.voiced, self.silent = 1, 0
            return None
        self.frames.append(frame)
        self.voiced += loud
        self.silent = 0 if loud else self.silent + 1
        if self.silent < END_SILENCE_FRAMES and len(self.frames) < MAX_PHRASE_FRAMES:
            return None
        phrase, enough = b"".join(self.frames), self.voiced >= MIN_VOICED_FRAMES
        self.reset()
        return phrase if enough else None


def after_wake(text):
    """Текст после «Юна» в начале фразы. `None`: фраза не к ней. Пустая строка: позвали без команды."""
    match = re.match(r"\s*([\w'’-]+)[\s,.!?:—-]*(.*)", text or "", re.S)
    if not match or match.group(1).lower() not in WAKE_WORDS:
        return None
    return match.group(2).strip()
