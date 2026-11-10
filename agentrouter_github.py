"""Local GitHub-account onboarding and daily AgentRouter OAuth check-in."""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from utils.config import AccountConfig, AppConfig
from utils.github_oauth import GithubLoginRequired, profile_directory, run_github_checkin


async def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('command', choices=('bootstrap', 'run', 'status'))
	parser.add_argument('--name', default='main', help='Stable local account name')
	parser.add_argument('--user-id', help='Optional expected AgentRouter user ID')
	args = parser.parse_args()
	root = Path(__file__).resolve().parent
	os.chdir(root)
	load_dotenv(root / '.env')
	if hasattr(sys.stdout, 'reconfigure'):
		sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
	profile = profile_directory(args.name)
	if args.command == 'status':
		print(json.dumps({'name': args.name, 'initialized': (profile / 'identity.json').exists()}, ensure_ascii=False))
		return 0
	account = AccountConfig(
		cookies=None, name=args.name, provider='agentrouter', api_user=args.user_id, auth_method='github'
	)
	provider = AppConfig.load_from_env().get_provider('agentrouter')
	assert provider is not None
	state_dir = root / '.local_state'
	state_dir.mkdir(exist_ok=True)
	# Exclusive creation prevents overlapping manual and scheduled runs.
	lock_path = state_dir / 'github.lock'
	try:
		lock = lock_path.open('x')
	except FileExistsError:
		print('[BUSY] Another GitHub check-in is running. If a run crashed, remove .local_state/github.lock.')
		return 3
	try:
		with lock:
			lock.write(str(os.getpid()))
			result = await run_github_checkin(account, provider, bootstrap=args.command == 'bootstrap')
		output = {
			'time': datetime.now().astimezone().isoformat(),
			'name': args.name,
			'login_verified': True,
			'checkin_status': result.status,
			'quota_change': result.reward,
			'balance': round(result.after['quota'] / 500000, 2),
		}
		(state_dir / 'last-result.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
		print(json.dumps(output, ensure_ascii=False, indent=2))
		if result.status == 'login_verified_reward_unknown':
			print('[INFO] Login succeeded. The server supplied no reward evidence; no credit claim is made.')
		return 0
	except GithubLoginRequired as exc:
		print(f'[ACTION REQUIRED] {exc}')
		return 2
	except Exception as exc:
		# Never print OAuth callback URLs, cookies or raw response payloads.
		print(f'[FAILED] {type(exc).__name__}. Check network access and rerun bootstrap if login is required.')
		return 1
	finally:
		lock_path.unlink(missing_ok=True)


if __name__ == '__main__':
	try:
		sys.exit(asyncio.run(main()))
	except KeyboardInterrupt:
		sys.exit(130)
