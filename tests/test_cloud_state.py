import base64
import json

import pytest
from cryptography.exceptions import InvalidTag

from utils.cloud_state import decrypt_state, encrypt_state, new_key, scoped_storage_state, validate_payload


def payload():
	return {
		'version': 1,
		'name': 'main',
		'domain': 'https://agentrouter.org',
		'user_id': '123',
		'storage_state': {
			'cookies': [{'name': 'user_session', 'value': 'session-fixture-only', 'domain': 'github.com'}],
			'origins': [],
		},
	}


def test_encrypted_state_round_trip_and_no_plaintext_credentials():
	key = new_key()
	state = payload()
	encrypted = encrypt_state(state, key)
	assert 'session-fixture-only' not in encrypted
	assert decrypt_state(encrypted, key) == state
	assert encrypt_state(state, key) != encrypted  # Fresh nonce for every export.


def test_modified_ciphertext_and_wrong_key_are_rejected():
	key = new_key()
	envelope = json.loads(encrypt_state(payload(), key))
	data = bytearray(base64.b64decode(envelope['data']))
	data[0] ^= 1
	envelope['data'] = base64.b64encode(data).decode()
	with pytest.raises(InvalidTag):
		decrypt_state(json.dumps(envelope), key)
	with pytest.raises(InvalidTag):
		decrypt_state(encrypt_state(payload(), key), new_key())


@pytest.mark.parametrize(
	'field,value', [('version', 2), ('name', 'other'), ('domain', 'https://evil.example'), ('user_id', '')]
)
def test_identity_or_destination_changes_are_rejected(field, value):
	state = payload()
	state[field] = value
	with pytest.raises(ValueError):
		validate_payload(state)


def test_export_removes_other_domains_github_storage_and_private_user_fields():
	state = payload()['storage_state']
	state['cookies'].extend(
		[
			{'name': 'other', 'value': 'secret', 'domain': 'other.example'},
			{'name': 'other', 'value': 'secret', 'domain': 'github.com.evil.example'},
		]
	)
	state['origins'] = [
		{'origin': 'https://github.com', 'localStorage': [{'name': 'private', 'value': 'secret'}]},
		{
			'origin': 'https://agentrouter.org',
			'localStorage': [
				{
					'name': 'user',
					'value': json.dumps({'id': 123, 'username': 'test', 'access_token': 'secret', 'email': 'private'}),
				},
				{'name': 'other', 'value': 'secret'},
			],
		},
	]
	scoped = scoped_storage_state(state)
	assert len(scoped['cookies']) == 1
	assert 'secret' not in json.dumps(scoped)
	assert json.loads(scoped['origins'][0]['localStorage'][0]['value']) == {'id': 123, 'username': 'test'}
	with pytest.raises(ValueError):
		validate_payload({**payload(), 'storage_state': state})


def test_missing_github_session_is_not_exportable():
	with pytest.raises(ValueError, match='no GitHub login'):
		scoped_storage_state({'cookies': [], 'origins': []})


def test_key_length_is_enforced():
	with pytest.raises(ValueError, match='256-bit'):
		encrypt_state(payload(), base64.b64encode(b'short').decode())
