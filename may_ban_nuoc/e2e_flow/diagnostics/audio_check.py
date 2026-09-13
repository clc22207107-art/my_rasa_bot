"""Test the actual VoiceServer capture and STT locally, without Rasa or orders."""
import argparse
import json
from pathlib import Path
import time
import uuid

from ..common import load_config, utc_now
from ..voice_server import VoiceServer


class LocalEvents:
    def __init__(self, output):
        self.path = output/'voice.journal.jsonl'

    def emit(self, name, data=None, context=None, level='INFO'):
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(time=utc_now(), event_name=name, event_data=data or {},
                                   context=context, level=level), ensure_ascii=False)+'\n')
        if name in ('vad.noise.calibrated', 'stt.completed', 'voice.error'):
            print(name, data, flush=True)

    def flush(self):
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/"config.json")
    parser.add_argument('--text', default='Hello')
    args = parser.parse_args()
    cfg = load_config(args.config)
    output = Path(__file__).resolve().parents[1]/'runs'/('audio_check_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    print('Output:', output, flush=True)
    voice = VoiceServer(cfg, LocalEvents(output))
    context = dict(session_id='audio_check', turn_id='turn_001')
    def wait(state):
        deadline = time.monotonic()+cfg['operation_timeout_seconds']
        while time.monotonic()<deadline:
            status=voice.dispatch('GET', '/status', {})
            if status['state']=='ERROR':
                raise RuntimeError(status['error'])
            if status['state']==state:
                return status
            time.sleep(.02)
        raise TimeoutError('Waiting for '+state)
    try:
        voice.dispatch('POST','/prepare',dict(context=context,text=args.text))
        wait('PREPARED')
        voice.dispatch('POST','/arm',dict(context=context))
        wait('LISTENING')
        voice.dispatch('POST','/prompt',dict(context=context))
        result=wait('CAPTURED')['result']
        (output/'result.json').write_text(json.dumps(result,indent=2))
        print('Capture and STT completed:', json.dumps(result), flush=True)
    finally:
        voice.cancel.set()
        for thread in (voice.worker, voice.prompt_worker):
            if thread:
                thread.join(3)


if __name__=='__main__':
    main()
