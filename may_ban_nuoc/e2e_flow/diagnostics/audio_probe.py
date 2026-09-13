"""Standalone acoustic probe; never connects to Rasa or changes audio routing."""
import argparse
import json
from pathlib import Path
import threading
import time
import uuid
import wave

import numpy as np
import sounddevice as sd
import webrtcvad
from piper import PiperVoice

from ..common import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/"config.json")
    parser.add_argument('--input-device')
    parser.add_argument('--output-device')
    args = parser.parse_args()
    cfg = load_config(args.config)
    inp = args.input_device or cfg['input_device']
    out = args.output_device or cfg['output_device']
    sr, n = cfg['sample_rate'], cfg['sample_rate']*30//1000
    output = Path(__file__).resolve().parents[1]/'runs'/('audio_probe_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    voice = PiperVoice.load(str(Path(cfg['piper_model']).expanduser()))
    audio = np.concatenate([c.audio_float_array for c in voice.synthesize('Hello')]).astype('float32')*cfg['volume']
    frames, rows, playback, errors = [], [], {}, []
    vad = webrtcvad.Vad(cfg['vad_aggressiveness'])
    def play():
        try:
            time.sleep(1.5)
            with sd.OutputStream(device=out, channels=1, dtype='float32', samplerate=voice.config.sample_rate) as stream:
                playback['start'] = time.monotonic()-start
                stream.write(audio)
                stream.stop()
                playback['end'] = time.monotonic()-start
        except Exception as exc:
            errors.append(str(exc))
    print('Output:', output, flush=True)
    print('Recording 6 seconds; playing Hello after 1.5 seconds.', flush=True)
    with sd.InputStream(device=inp, channels=1, dtype='int16', samplerate=sr, blocksize=n) as stream:
        start = time.monotonic()
        thread = threading.Thread(target=play)
        thread.start()
        for _ in range(200):
            frame, overflow = stream.read(n)
            frames.append(frame.copy())
            x = frame.astype('float64')/32768.0
            rows.append(dict(t=time.monotonic()-start, dbfs=float(20*np.log10(max(1e-9, np.sqrt(np.mean(x*x))))),
                             dc=float(np.mean(x)), peak=float(np.max(np.abs(x))),
                             speech=vad.is_speech(frame.tobytes(),sr), overflow=bool(overflow)))
        thread.join(3)
    with wave.open(str(output/'microphone.wav'), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.concatenate(frames).astype('<i2').tobytes())
    summary = dict(input=str(sd.query_devices(inp, 'input')), output=str(sd.query_devices(out, 'output')),
                   playback=playback, errors=errors)
    for name, subset in [('before', [r for r in rows if .4<r['t']<1.4]),
                         ('during', [r for r in rows if playback.get('start',999)<r['t']<playback.get('end',0)]),
                         ('after', [r for r in rows if r['t']>3])]:
        if subset:
            summary[name] = dict(median_dbfs=float(np.median([r['dbfs'] for r in subset])),
                                 speech_ratio=float(np.mean([r['speech'] for r in subset])),
                                 mean_dc=float(np.mean([r['dc'] for r in subset])),
                                 max_peak=max(r['peak'] for r in subset))
    (output/'probe.json').write_text(json.dumps(dict(summary=summary, frames=rows), indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
