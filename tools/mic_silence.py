"""Разбор вывода silencedetect из ffmpeg: когда пора заканчивать запись."""
import re

_START = re.compile(r"silence_start: ([\d.]+)")
# Тишина с самого начала ещё не значит конец фразы: человек мог не успеть заговорить.
_MIN_SPEECH_SECS = 0.4


def phrase_ended(line):
    """Истина, если тишина наступила после речи."""
    m = _START.search(line)
    return bool(m) and float(m.group(1)) > _MIN_SPEECH_SECS
