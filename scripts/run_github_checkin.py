"""Scheduled entry point: bounded logs, no terminal window, propagate failures."""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
	name = sys.argv[1] if len(sys.argv) > 1 else 'main'
	log_dir = ROOT / 'logs'
	log_dir.mkdir(exist_ok=True)
	# Keep 30 daily logs, none of which contains passwords, cookies or OAuth URLs.
	for old in sorted(log_dir.glob('github-*.log'))[:-29]:
		old.unlink()
	log_path = log_dir / f'github-{datetime.now():%Y-%m-%d}.log'
	python = Path(sys.executable)
	if python.name.lower() == 'pythonw.exe':
		python = python.with_name('python.exe')
	env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
	with log_path.open('a', encoding='utf-8') as log:
		log.write(f'\n[{datetime.now().astimezone().isoformat()}] Starting GitHub OAuth check-in\n')
		log.flush()
		try:
			result = subprocess.run(
				[str(python), str(ROOT / 'agentrouter_github.py'), 'run', '--name', name],
				cwd=ROOT,
				env=env,
				stdout=log,
				stderr=subprocess.STDOUT,
				timeout=300,
				creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
				check=False,
			)
			return result.returncode
		except subprocess.TimeoutExpired:
			log.write('[FAILED] Check-in exceeded the 5-minute execution limit.\n')
			return 1


if __name__ == '__main__':
	sys.exit(main())
