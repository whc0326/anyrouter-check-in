"""Encrypted GitHub session export, public preflight, and cloud OAuth check-in."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import ProxySettings, async_playwright

from utils.cloud_state import DOMAIN, decode_key, decrypt_state, encrypt_state, new_key, scoped_storage_state
from utils.config import AccountConfig, ProviderConfig
from utils.github_oauth import GithubLoginRequired, perform_github_checkin, profile_directory

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / '.local_state' / 'cloud'


async def launch_cloud_browser(playwright, proxy=None):
	engine = os.getenv('CLOUD_BROWSER_ENGINE', 'chrome')
	if engine == 'cloak':
		from cloakbrowser import launch_async

		return await launch_async(headless=True, proxy=proxy)
	if engine != 'chrome':
		raise ValueError('Unsupported cloud browser engine')
	return await playwright.chromium.launch(
		channel=os.getenv('CHECKIN_BROWSER_CHANNEL', 'chrome'),
		headless=True,
		proxy=proxy,
	)


async def export_state(name: str):
	profile_dir = profile_directory(name)
	identity_path = profile_dir / 'identity.json'
	if not identity_path.exists():
		raise GithubLoginRequired('Run agentrouter_github.py bootstrap before exporting a cloud session')
	identity = json.loads(identity_path.read_text(encoding='utf-8'))
	channel = os.getenv('CHECKIN_BROWSER_CHANNEL', 'chrome' if sys.platform == 'win32' else '')
	async with async_playwright() as playwright:
		context = await playwright.chromium.launch_persistent_context(
			str(profile_dir), channel=channel or None, headless=True
		)
		try:
			state = scoped_storage_state(await context.storage_state())
		finally:
			await context.close()
	key_path = STATE_DIR / 'state-key.txt'
	key = key_path.read_text(encoding='utf-8').strip() if key_path.exists() else new_key()
	payload = {'version': 1, 'name': name, 'domain': DOMAIN, 'user_id': str(identity['id']), 'storage_state': state}
	STATE_DIR.mkdir(parents=True, exist_ok=True)
	for path, content in ((key_path, key), (STATE_DIR / 'initial-state.enc', encrypt_state(payload, key))):
		path.write_text(content, encoding='utf-8')
		path.chmod(0o600)
	print('[READY] Encrypted cloud state and key saved locally under .local_state/cloud/. Nothing was uploaded.')
	print(json.dumps({'name': name, 'cookie_count': len(state['cookies']), 'sites': ['github.com', 'agentrouter.org']}))


async def preflight():
	async with async_playwright() as playwright:
		browser = await launch_cloud_browser(playwright)
		try:
			page = await browser.new_page()
			await page.goto('https://github.com/login', wait_until='domcontentloaded', timeout=60_000)
			print('[PREFLIGHT] GitHub public login page reached')
			await page.goto(f'{DOMAIN}/login', wait_until='domcontentloaded', timeout=60_000)
			try:
				await page.get_by_role('button', name=re.compile('GitHub', re.I)).wait_for(
					state='visible', timeout=45_000
				)
			except Exception:
				STATE_DIR.mkdir(parents=True, exist_ok=True)
				await page.screenshot(path=str(STATE_DIR / 'preflight.png'))
				raise RuntimeError(
					'AgentRouter login button unavailable in this runner; network or verification blocked'
				) from None
			print('[PREFLIGHT] AgentRouter GitHub sign-in button reached without account credentials')
		finally:
			await browser.close()


async def cloud_run(name: str):
	key = os.environ.get('CLOUD_STATE_KEY', '')
	restored = STATE_DIR / 'restored' / 'state.enc'
	encrypted = restored.read_text(encoding='utf-8') if restored.exists() else os.environ.get('CLOUD_LOGIN_STATE', '')
	if not key or not encrypted:
		raise ValueError('CLOUD_STATE_KEY and CLOUD_LOGIN_STATE must be configured before a cloud run')
	payload = decrypt_state(encrypted, key, name)
	account = AccountConfig(
		cookies=None, name=name, provider='agentrouter', auth_method='github', api_user=payload['user_id']
	)
	provider = ProviderConfig(name='agentrouter', domain=DOMAIN, sign_in_path=None)
	proxy: ProxySettings | None = None
	if os.getenv('CHECKIN_PROXY_URL'):
		proxy = {'server': os.environ['CHECKIN_PROXY_URL']}
	async with async_playwright() as playwright:
		browser = await launch_cloud_browser(playwright, proxy)
		context = await browser.new_context(
			storage_state=payload['storage_state'], viewport={'width': 1280, 'height': 900}
		)
		try:
			result = await perform_github_checkin(context, account, provider, expected_id=str(payload['user_id']))
			payload['storage_state'] = scoped_storage_state(await context.storage_state())
			STATE_DIR.mkdir(parents=True, exist_ok=True)
			(STATE_DIR / 'state.enc').write_text(encrypt_state(payload, key), encoding='utf-8')
			# No account ID, balance, cookies, or OAuth URLs are written to public logs.
			report = {
				'time': datetime.now(timezone.utc).isoformat(),
				'login_verified': True,
				'checkin_status': result.status,
			}
			print(json.dumps(report))
			if os.getenv('GITHUB_STEP_SUMMARY'):
				with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
					summary.write(
						f'GitHub OAuth login and account identity verified. Reward status: `{result.status}`.\n'
					)
		finally:
			await context.close()
			await browser.close()


async def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument('command', choices=('export', 'preflight', 'run', 'key-id'))
	parser.add_argument('--name', default='main')
	args = parser.parse_args()
	os.chdir(ROOT)
	if hasattr(sys.stdout, 'reconfigure'):
		sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
	try:
		if args.command == 'key-id':
			key_id = hashlib.sha256(decode_key(os.environ['CLOUD_STATE_KEY'])).hexdigest()[:16]
			with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
				output.write(f'key-id={key_id}\n')
		elif args.command == 'export':
			await export_state(args.name)
		elif args.command == 'preflight':
			await preflight()
		else:
			await cloud_run(args.name)
		return 0
	except GithubLoginRequired:
		print(
			'[ACTION REQUIRED] GitHub requested sign-in or verification. Renew the local login and export cloud state again.'
		)
		return 2
	except Exception as exc:
		print(f'[FAILED] {type(exc).__name__}. Credentials, raw responses and OAuth URLs are intentionally omitted.')
		return 1


if __name__ == '__main__':
	sys.exit(asyncio.run(main()))
