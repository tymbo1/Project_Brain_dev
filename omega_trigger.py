import json
from datetime import datetime

def run_omega_tasks(command='OT', capsule_path='capsules/omega_state.json'):
    log_path = 'logs/omega_trigger.log'
    now = datetime.now().isoformat()
    response = {
        'status': 'ok',
        'time': now,
        'command': command,
        'actions': []
    }

    try:
        if command == 'OT':
            actions = [
                "Inference memory logs initialized",
                "Strength divergence seeds injected",
                "Live glyph feedback activated",
                "Braid mode initialized",
                "Omega persistence logging enabled"
            ]
            with open(capsule_path, 'a') as f:
                capsule = {
                    'time': now,
                    'trigger': 'omega_tasks',
                    'actions': actions
                }
                f.write(json.dumps(capsule) + '\n')
            response['actions'] = actions
        else:
            response['status'] = 'unknown_command'

    except Exception as e:
        response['status'] = 'error'
        response['error'] = str(e)

    with open(log_path, 'a') as log:
        log.write(json.dumps(response) + '\n')

    return response
