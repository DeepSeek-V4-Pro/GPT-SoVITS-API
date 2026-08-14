"""
音频编码工具
============
将合成得到的浮点波形数组打包为 wav / ogg / aac / raw 格式，
以及流式 WAV 头（未知总长度时用 uint32 最大值占位）。
"""

import struct
import subprocess
from io import BytesIO

import numpy as np
import soundfile as sf


def _as_int16(data: np.ndarray) -> np.ndarray:
    """统一转为 16-bit PCM 样本（浮点 [-1,1] 缩放裁剪；int16 原样返回）。

    手机浏览器（尤其 iOS Safari）对 float32 WAV 支持差，16-bit PCM 兼容性最好。
    """
    if data.dtype == np.int16:
        return data
    data = np.asarray(data, dtype=np.float32)
    data = np.clip(data, -1.0, 1.0)
    return (data * 32767.0).astype(np.int16)


def pack_ogg(io_buffer: BytesIO, data: np.ndarray, rate: int) -> BytesIO:
    sf.write(io_buffer, data, rate, format="ogg")
    return io_buffer


def pack_wav(io_buffer: BytesIO, data: np.ndarray, rate: int) -> BytesIO:
    sf.write(io_buffer, _as_int16(data), rate, format="wav", subtype="PCM_16")
    return io_buffer


def pack_raw(io_buffer: BytesIO, data: np.ndarray, rate: int) -> BytesIO:
    io_buffer.write(data.tobytes())
    return io_buffer


def pack_aac(io_buffer: BytesIO, data: np.ndarray, rate: int) -> BytesIO:
    if not isinstance(rate, (int, np.integer)) or int(rate) < 8000 or int(rate) > 384000:
        rate = 32000
    rate = int(rate)
    process = subprocess.Popen(
        ["ffmpeg", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
         "-c:a", "aac", "-b:a", "192k", "-vn", "-f", "adts", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out, _ = process.communicate(input=data.tobytes())
    io_buffer.write(out)
    return io_buffer


def pack_audio(io_buffer: BytesIO, data: np.ndarray, rate: int, media_type: str) -> BytesIO:
    packers = {"ogg": pack_ogg, "wav": pack_wav, "aac": pack_aac}
    io_buffer = packers.get(media_type, pack_raw)(io_buffer, data, rate)
    io_buffer.seek(0)
    return io_buffer


def _wav_stream_header(sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    """WAV header for streaming (unknown total size = uint32 max)."""
    block_align = channels * sample_width
    byte_rate = sample_rate * block_align
    data_size = 2 ** 32 - 1
    file_size = data_size + 36
    return struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', file_size, b'WAVE',
        b'fmt ', 16, 1, channels, sample_rate, byte_rate, block_align, sample_width * 8,
        b'data', data_size,
    )
