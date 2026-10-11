"""Read-only audit probes. No connection to the application database."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
os.environ['RUN_INLINE_NOTIFICATION_WORKER'] = 'False'
import django
from django.conf import settings
settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
settings.LOCAL_AUTH_BYPASS = False
django.setup()
from django.core.management import call_command
from django.urls import URLResolver, get_resolver
from apps.accounts.forms import SecurePasswordChangeForm
from apps.accounts.models import User

call_command('check')
call_command('makemigrations', check=True, dry_run=True)

routes = []
def walk(patterns, prefix='', namespace=''):
    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            if entry.app_name != 'admin':
                walk(entry.url_patterns, route, namespace + (entry.namespace + ':' if entry.namespace else ''))
        elif entry.name:
            routes.append({'path': '/' + route, 'name': namespace + entry.name,
                           'view': entry.lookup_str})
walk(get_resolver().url_patterns)

user = User(email='audit@example.test', totp_enabled=False)
user.set_password('Audit-current-4398!')
form = SecurePasswordChangeForm(user=user, data={
    'old_password': 'Audit-current-4398!',
    'new_password1': 'Replacement-7834-unique!',
    'new_password2': 'Replacement-7834-unique!',
})
form.is_valid()

env = dict(os.environ, ENVIRONMENT='production', DEBUG='False', MFA_ENABLED='False',
           LOCAL_AUTH_BYPASS='False', DB_HOST='audit-no-connection.invalid',
           DJANGO_SECRET_KEY='audit-probe-not-a-production-secret')
probe = subprocess.run([sys.executable, '-c',
    'try:\n import config.settings\n print("accepted")\nexcept Exception as exc:\n print(type(exc).__name__ + ": " + str(exc))'],
    cwd=ROOT, env=env, capture_output=True, text=True)

files = sorted([*ROOT.glob('apps/**/*.py'), *ROOT.glob('config/*.py'),
                *ROOT.glob('static_src/css/*.css'), *ROOT.glob('templates/**/*.html')])
manifest = {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files}
models = []
for p in ROOT.glob('apps/*/models.py'):
    tree = ast.parse(p.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
            isinstance(b, ast.Attribute) and b.attr == 'Model' for b in node.bases):
            models.append({'app': p.parent.name, 'model': node.name, 'line': node.lineno})

def luminance(hex_color):
    values = [int(hex_color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in values]
    return sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))
def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return round((y + .05) / (x + .05), 2)
colors = ['#006b4f', '#013b36', '#c9a227', '#18201f', '#5f6b67', '#b7791f', '#c0392b']
evidence = {
    'date': '2026-10-09', 'scope': 'local working tree; no deployed instance or financial balances inspected',
    'python': sys.version.split()[0], 'django': django.get_version(),
    'checks': {'django_system_check': 'passed', 'migration_drift': 'none',
               'production_mfa_disabled_import': probe.stdout.strip(),
               'password_without_totp': {'valid': not bool(form.errors),
                                        'errors': form.errors.get_json_data()}},
    'previous_session_suite': {'tests': 198, 'passed': 188, 'failed': 7, 'skipped': 3,
                               'database': 'isolated in-memory SQLite', 'rerun_in_this_probe': False},
    'routes': routes, 'models': models,
    'contrast_on_white': {c: contrast(c, '#ffffff') for c in colors},
    'source_sha256': manifest,
}
(OUT / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: evidence[k] for k in ('python', 'django', 'checks', 'contrast_on_white')}, ensure_ascii=False))
print(f'Inventaire : {len(routes)} routes nommées hors admin, {len(models)} modèles directs, {len(files)} empreintes.')
