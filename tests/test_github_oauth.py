import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils.config import AccountConfig, AppConfig, load_accounts_config
from utils.github_oauth import (
	GithubLoginRequired,
	ResponseCapture,
	github_auth_required,
	profile_directory,
	reward_evidence,
	run_github_checkin,
	user_profile,
)


def test_github_accounts_need_no_email_password_or_cookie(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS', json.dumps([{'name': 'main', 'provider': 'agentrouter', 'auth_method': 'github'}])
	)
	accounts = load_accounts_config()
	assert accounts is not None
	assert accounts[0].auth_method == 'github'
	assert accounts[0].cookies is None


@pytest.mark.parametrize(
	'config',
	[
		{'provider': 'agentrouter', 'auth_method': 'github'},
		{'name': 'main', 'provider': 'anyrouter', 'auth_method': 'github'},
		{'name': 'main', 'provider': 'agentrouter', 'auth_method': 'typo'},
	],
)
def test_invalid_github_configuration_fails(monkeypatch, config):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([config]))
	assert load_accounts_config() is None


def test_untrusted_account_names_cannot_escape_profile_root(tmp_path, monkeypatch):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	profile = profile_directory('../../other-profile')
	assert profile.parent == tmp_path / 'github'
	assert profile != profile_directory('other-profile')


def test_http_success_and_unchanged_balance_do_not_prove_checkin():
	profile = {'id': 1, 'quota': 1000000, 'used_quota': 0}
	assert reward_evidence({'success': True}, profile, profile) == ('login_verified_reward_unknown', 0.0)
	assert reward_evidence({'success': True, 'check_in': False}, profile, profile) == ('no_new_reward', 0.0)


def test_usage_does_not_hide_reward_and_does_not_create_fake_reward():
	before = {'id': 1, 'quota': 50000000, 'used_quota': 10000000}
	after = {'id': 1, 'quota': 62000000, 'used_quota': 10500000}
	assert reward_evidence({'success': True}, before, after) == ('reward_confirmed', 25.0)
	spent = {'id': 1, 'quota': 49500000, 'used_quota': 10500000}
	assert reward_evidence({'success': True}, before, spent) == ('login_verified_reward_unknown', 0.0)


def test_cross_account_balances_are_not_compared():
	before = {'id': 1, 'quota': 0, 'used_quota': 0}
	after = {'id': 2, 'quota': 12500000, 'used_quota': 0}
	assert reward_evidence({'success': True}, before, after) == ('login_verified_reward_unknown', None)


def test_server_flag_can_confirm_first_run_without_baseline():
	after = {'id': 1, 'quota': 12500000, 'used_quota': 0}
	assert reward_evidence({'success': True, 'data': {'check_in': True}}, None, after) == ('reward_confirmed', None)


@pytest.mark.parametrize(
	'url,expected',
	[
		('https://github.com/login?return_to=xxx', True),
		('https://github.com/sessions/two-factor', True),
		('https://github.com/login/device', True),
		('https://github.com/login.evil.com/', False),
		('https://github.com.evil.com/login', False),
		('https://github.com/login/oauth/authorize', False),
		('https://agentrouter.org/login', False),
	],
)
def test_expired_auth_detection_is_origin_scoped(url, expected):
	assert github_auth_required(url) is expected


@pytest.mark.asyncio
async def test_capture_rejects_html_errors_and_unrelated_origin():
	capture = ResponseCapture('https://agentrouter.org', '/api/user/self')
	for url, status, payload in [
		('https://evil.example/api/user/self', 200, {'success': True, 'data': {'id': 3}}),
		('https://agentrouter.org/api/user/self', 401, {'success': False}),
		('https://agentrouter.org/api/user/self', 200, ValueError('HTML challenge')),
	]:
		response = SimpleNamespace(url=url, status=status, json=AsyncMock())
		if isinstance(payload, Exception):
			response.json.side_effect = payload
		else:
			response.json.return_value = payload
		await capture.consume(response)
	assert capture.profiles == []
	assert capture.callbacks == []


@pytest.mark.asyncio
async def test_capture_accepts_only_fresh_valid_profiles_and_callback():
	capture = ResponseCapture('https://agentrouter.org', '/api/user/self')
	await capture.consume(
		SimpleNamespace(
			url='https://agentrouter.org/api/oauth/github?code=secret',
			status=200,
			json=AsyncMock(return_value={'success': True, 'check_in': False}),
		)
	)
	await capture.consume(
		SimpleNamespace(
			url='https://agentrouter.org/api/user/self',
			status=200,
			json=AsyncMock(
				return_value={'success': True, 'data': {'id': 3, 'quota': 10, 'used_quota': 5, 'password': 'hidden'}}
			),
		)
	)
	assert capture.callbacks == [{'success': True, 'check_in': False}]
	assert capture.profiles == [{'id': 3, 'quota': 10, 'used_quota': 5}]
	assert user_profile({'success': True, 'data': {'id': 3}}) is None


@pytest.mark.asyncio
async def test_unbootstrapped_account_fails_before_launching_browser(tmp_path, monkeypatch):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
	account = AccountConfig(cookies=None, name='main', provider='agentrouter', auth_method='github')
	provider = AppConfig.load_from_env().get_provider('agentrouter')
	with pytest.raises(GithubLoginRequired, match='bootstrap'):
		await run_github_checkin(account, provider)


@pytest.mark.asyncio
async def test_agentrouter_cookie_mode_cannot_report_fake_success(monkeypatch):
	from checkin import check_in_account

	account = AccountConfig(cookies={'session': 'placeholder'}, api_user='1', provider='agentrouter')
	result = await check_in_account(account, 0, AppConfig.load_from_env())
	assert result == (False, None, None)


def test_unknown_reward_is_not_labeled_as_already_checked_in():
	from checkin import format_check_in_notification

	message = format_check_in_notification(
		{
			'name': 'main',
			'checkin_status': 'login_verified_reward_unknown',
			'after_quota': 10,
		}
	)
	assert '尚未确认' in message
	assert '已签到' not in message
