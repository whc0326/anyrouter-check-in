"""AgentRouter GitHub OAuth with an isolated, local browser profile.

No GitHub password or browser session is uploaded to Actions. Bootstrap is
interactive; unattended runs stop when GitHub requires authentication again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import ProxySettings, async_playwright


class GithubLoginRequired(RuntimeError):
	pass


def profile_directory(name: str) -> Path:
	root = Path(os.getenv('CHECKIN_BROWSER_PROFILE_DIR', '.browser_profiles')).resolve()
	# Account names must never be interpreted as filesystem paths.
	key = hashlib.sha256(name.encode('utf-8')).hexdigest()[:20]
	return root / 'github' / key


def user_profile(payload: object) -> dict | None:
	if not isinstance(payload, dict) or payload.get('success') is not True:
		return None
	data = payload.get('data')
	if not isinstance(data, dict) or not data.get('id'):
		return None
	if not all(isinstance(data.get(key), (int, float)) for key in ('quota', 'used_quota')):
		return None
	return {key: data[key] for key in ('id', 'quota', 'used_quota')}


def reward_evidence(callback: dict, before: dict | None, after: dict) -> tuple[str, float | None]:
	"""Only a server check-in flag or fresh total-quota increase proves a reward."""
	data = callback.get('data')
	flag = callback.get('check_in')
	if not isinstance(flag, bool) and isinstance(data, dict):
		flag = data.get('check_in')
	delta = None
	if before is not None and str(before['id']) == str(after['id']):
		delta = (after['quota'] + after['used_quota'] - before['quota'] - before['used_quota']) / 500000
	if flag is True or (delta is not None and delta > 0.000001):
		return 'reward_confirmed', delta
	# A false flag does not prove a previous reward; it only says no new reward.
	if flag is False:
		return 'no_new_reward', delta
	return 'login_verified_reward_unknown', delta


@dataclass
class GithubCheckinResult:
	before: dict | None
	after: dict
	status: str
	reward: float | None


class ResponseCapture:
	"""Capture only same-origin OAuth/self responses, including popup pages."""

	def __init__(self, domain: str, user_path: str):
		self.origin = (urlsplit(domain).scheme, urlsplit(domain).netloc)
		self.user_path = user_path
		self.callbacks: list[dict] = []
		self.profiles: list[dict] = []
		self.pending: set[asyncio.Task] = set()

	def on_response(self, response):
		task = asyncio.create_task(self.consume(response))
		self.pending.add(task)
		task.add_done_callback(self.pending.discard)

	async def consume(self, response):
		url = urlsplit(response.url)
		if (url.scheme, url.netloc) != self.origin or response.status != 200:
			return
		is_callback = url.path == '/api/oauth/github'
		if url.path != self.user_path and not is_callback:
			return
		try:
			payload = await asyncio.wait_for(response.json(), timeout=10)
		except Exception:
			return  # HTML challenges are never treated as login or quota data.
		if is_callback:
			if isinstance(payload, dict):
				self.callbacks.append(payload)
		else:
			profile = user_profile(payload)
			if profile:
				self.profiles.append(profile)

	async def settle(self):
		if self.pending:
			await asyncio.gather(*list(self.pending), return_exceptions=True)


def github_auth_required(url: str) -> bool:
	parsed = urlsplit(url)
	if parsed.hostname != 'github.com' or parsed.path.startswith('/login/oauth/'):
		return False
	return parsed.path == '/login' or parsed.path.startswith(
		('/login/device', '/session', '/sessions', '/two-factor', '/settings/two_factor', '/sudo')
	)


async def _click_github(page, timeout_ms: int):
	button = page.get_by_role('button', name=re.compile(r'GitHub', re.I))
	await button.wait_for(state='visible', timeout=timeout_ms)
	await button.click(timeout=timeout_ms)


async def run_github_checkin(account, provider, *, bootstrap: bool = False) -> GithubCheckinResult:
	if account.provider != 'agentrouter':
		raise ValueError('GitHub OAuth is currently supported only for agentrouter')
	if os.getenv('GITHUB_ACTIONS') == 'true' and os.getenv('RUNNER_ENVIRONMENT') != 'self-hosted':
		raise GithubLoginRequired('GitHub-hosted runners have no local GitHub login. Run this account locally.')
	name = account.name or 'main'
	profile_dir = profile_directory(name)
	identity_path = profile_dir / 'identity.json'
	identity = json.loads(identity_path.read_text(encoding='utf-8')) if identity_path.exists() else {}
	if not bootstrap and not identity.get('id'):
		raise GithubLoginRequired(f'First run: python agentrouter_github.py bootstrap --name "{name}"')
	expected_id = str(account.api_user or identity.get('id') or '')
	domain = provider.domain.rstrip('/')
	if urlsplit(domain).scheme != 'https':
		raise ValueError('GitHub OAuth requires an HTTPS provider domain')
	profile_dir.mkdir(parents=True, exist_ok=True)
	minutes = 10 if bootstrap else 2
	timeout_ms = int(os.getenv('CHECKIN_GITHUB_TIMEOUT_MS', str(minutes * 60_000)))
	headless = not bootstrap and os.getenv('CHECKIN_HEADLESS', 'true').lower() not in {'false', '0', 'no'}
	channel = os.getenv('CHECKIN_BROWSER_CHANNEL', 'chrome' if sys.platform == 'win32' else '')
	proxy: ProxySettings | None = None
	# Only use an explicitly configured proxy; never copy the user's proxy secrets.
	if provider.use_proxy and os.getenv('CHECKIN_PROXY_URL'):
		proxy = {'server': os.environ['CHECKIN_PROXY_URL']}
	async with async_playwright() as playwright:
		context = await playwright.chromium.launch_persistent_context(
			str(profile_dir),
			channel=channel or None,
			headless=headless,
			viewport={'width': 1280, 'height': 900},
			proxy=proxy,
		)
		capture = ResponseCapture(domain, provider.user_info_path)
		context.on('response', capture.on_response)
		try:
			page = context.pages[0] if context.pages else await context.new_page()
			await page.goto(f'{domain}/console', wait_until='domcontentloaded', timeout=60_000)
			# Read a fresh baseline when possible; never substitute a cached balance.
			try:
				await page.wait_for_timeout(1500)
				await capture.settle()
			except Exception:
				pass
			before = capture.profiles[-1] if capture.profiles else None
			if before and expected_id and str(before['id']) != expected_id:
				raise ValueError('The saved profile belongs to a different AgentRouter account')
			# Remove only AgentRouter authentication; GitHub's session remains local.
			for cookie in await context.cookies([domain]):
				if cookie['name'] == 'session':
					await context.clear_cookies(name='session', domain=cookie['domain'], path=cookie['path'])
			await page.evaluate("() => localStorage.removeItem('user')")
			await page.goto(f'{domain}{provider.login_path}', wait_until='domcontentloaded', timeout=60_000)
			callback_start, profile_start = len(capture.callbacks), len(capture.profiles)
			await _click_github(page, min(timeout_ms, 60_000))
			if bootstrap:
				print(
					'[ACTION REQUIRED] Sign in to GitHub in the opened browser and complete any verification.',
					flush=True,
				)
			deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
			callback = None
			authenticated_page = None
			while asyncio.get_running_loop().time() < deadline:
				await capture.settle()
				for candidate in context.pages:
					if not bootstrap and github_auth_required(candidate.url):
						raise GithubLoginRequired('GitHub session expired; run bootstrap again to sign in manually')
					if candidate.url.startswith(f'{domain}/console'):
						authenticated_page = candidate
				new_callbacks = capture.callbacks[callback_start:]
				if new_callbacks:
					callback = new_callbacks[-1]
					if callback.get('success') is not True:
						raise RuntimeError('AgentRouter rejected the GitHub OAuth callback')
				if callback and authenticated_page:
					break
				await asyncio.sleep(0.25)
			if not callback or not authenticated_page:
				raise GithubLoginRequired('OAuth did not finish. Complete login/verification manually using bootstrap.')
			# Trigger a fresh server-side profile request after OAuth, using the browser.
			capture.profiles = capture.profiles[:profile_start]
			await authenticated_page.goto(f'{domain}/console', wait_until='domcontentloaded', timeout=60_000)
			deadline = asyncio.get_running_loop().time() + 30
			while len(capture.profiles) <= profile_start and asyncio.get_running_loop().time() < deadline:
				await asyncio.sleep(0.25)
				await capture.settle()
			if len(capture.profiles) <= profile_start:
				raise RuntimeError('OAuth returned, but a fresh /api/user/self response could not be verified')
			after = capture.profiles[-1]
			if expected_id and str(after['id']) != expected_id:
				raise ValueError('GitHub signed in to a different AgentRouter account; profile was not accepted')
			identity_path.write_text(json.dumps({'id': after['id']}), encoding='utf-8')
			status, reward = reward_evidence(callback, before, after)
			return GithubCheckinResult(before, after, status, reward)
		finally:
			context.remove_listener('response', capture.on_response)
			await capture.settle()
			await context.close()
