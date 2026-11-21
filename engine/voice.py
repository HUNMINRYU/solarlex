import functools
import os

STT_MODEL = "gpt-transcribe"
TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = "marin"
MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_SPEECH_CHARS = 1000
FILE_EXTENSION = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/x-m4a": "m4a", "audio/mpeg": "mp3",
                  "audio/ogg": "ogg", "audio/wav": "wav"}


def available():
    return bool(os.environ.get("OPENAI_API_KEY"))


@functools.cache
def _client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=30)


def media_type(content_type):
    return content_type.split(";")[0].strip().lower()


def transcribe(audio, content_type):
    kind = media_type(content_type)
    recording = (f"question.{FILE_EXTENSION[kind]}", audio, kind)
    return _client().audio.transcriptions.create(file=recording, model=STT_MODEL, languages=["ko"]).text.strip()


def speak(text):
    return _client().audio.speech.create(input=text, model=TTS_MODEL, voice=TTS_VOICE, response_format="mp3").content
