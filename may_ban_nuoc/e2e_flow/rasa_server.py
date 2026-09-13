"""Terminal 3: Rasa with timestamps recorded inside its own request handler."""
import sys
from .common import load_config
from .support.runtime import arguments, client, instrument_http, instrument_method


def main():
    args = arguments(__doc__, model=True)
    if not args.model.is_file():
        raise FileNotFoundError(args.model)
    cfg = load_config(args.config)
    events = client('rasa', args, cfg)
    import rasa.server
    from rasa.core.processor import MessageProcessor
    from rasa.core.actions.action import RemoteAction
    original = rasa.server.create_app

    def create_app(*a, **kw):
        return instrument_http(original(*a, **kw), events, 'rasa')

    rasa.server.create_app = create_app
    instrument_method(MessageProcessor, 'parse_message', events, 'rasa.nlu',
                      lambda a, k: {'text': a[1].text})
    instrument_method(MessageProcessor, '_run_action', events, 'rasa.action',
                      lambda a, k: {'action_name': a[1].name(), 'slots': a[2].current_slot_values()},
                      lambda result, a: {'continue_prediction': result, 'action_name': a[1].name(),
                                         'slots': a[2].current_slot_values()})
    instrument_method(RemoteAction, 'run', events, 'rasa.action_call',
                      lambda a, k: {'action_name': a[0].name()})
    from tempfile import TemporaryDirectory
    from pathlib import Path
    temporary_config = TemporaryDirectory(prefix="e2e_rasa_")
    output = Path(temporary_config.name)
    endpoints = output/'endpoints.yml'
    endpoints.write_text(f"action_endpoint:\n  url: http://127.0.0.1:{cfg['ports']['action']}/webhook\n")
    credentials = output/'credentials.yml'
    credentials.write_text('rest:\n')
    # These wrappers are process-local; installed Rasa and business actions are unchanged.
    import os
    os.environ['SANIC_WORKERS'] = '1'
    sys.argv = ['rasa', 'run', '--enable-api', '--interface', '127.0.0.1',
                '--port', str(cfg['ports']['rasa']), '--model', str(args.model.resolve()),
                '--endpoints', str(endpoints), '--credentials', str(credentials)]
    events.emit('service.starting', {'model': str(args.model.resolve()), 'port': cfg['ports']['rasa']})
    from rasa.__main__ import main as rasa_main
    try:
        rasa_main()
    finally:
        temporary_config.cleanup()


if __name__ == '__main__':
    main()
