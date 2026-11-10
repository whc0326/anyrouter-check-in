"""Authenticated encryption and strict scoping for portable GitHub login state."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b'agentrouter-github-cloud-v1'
DOMAIN = 'https://agentrouter.org'


def new_key() -> str:
	return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode('ascii')


def decode_key(key: str) -> bytes:
	value = base64.b64decode(key.strip(), validate=True)
	if len(value) != 32:
		raise ValueError('Cloud state encryption requires a 256-bit key')
	return value


def scoped_storage_state(state: Mapping[str, Any]) -> dict:
	"""Export only the dedicated profile's GitHub and AgentRouter sessions."""
	allowed_domains = {'github.com', 'agentrouter.org'}
	cookies = [cookie for cookie in state.get('cookies', []) if cookie.get('domain', '').lstrip('.') in allowed_domains]
	if not any(cookie.get('name') == 'user_session' and cookie.get('value') for cookie in cookies):
		raise ValueError('The dedicated browser has no GitHub login session; bootstrap again')
	origins = []
	for origin in state.get('origins', []):
		if origin.get('origin') != DOMAIN:
			continue
		# GitHub local storage and unrelated site data must never be exported.
		for item in origin.get('localStorage', []):
			if item.get('name') != 'user':
				continue
			try:
				user = json.loads(item['value'])
			except (KeyError, TypeError, ValueError):
				continue
			if isinstance(user, dict):
				public_fields = {
					key: user[key]
					for key in ('id', 'username', 'display_name', 'role', 'status', 'group')
					if key in user
				}
				origins.append(
					{'origin': DOMAIN, 'localStorage': [{'name': 'user', 'value': json.dumps(public_fields)}]}
				)
	return {'cookies': cookies, 'origins': origins}


def validate_payload(payload: object, name: str = 'main') -> dict:
	if not isinstance(payload, dict) or payload.get('version') != 1 or payload.get('name') != name:
		raise ValueError('Cloud login state version or account name mismatch')
	if payload.get('domain') != DOMAIN or not str(payload.get('user_id', '')).isdigit():
		raise ValueError('Cloud login state has an invalid provider or account identity')
	state = payload.get('storage_state')
	if not isinstance(state, dict):
		raise ValueError('Cloud login state is missing browser storage')
	allowed = scoped_storage_state(state)
	# Reject rather than silently accept cross-origin or extra local storage data.
	if allowed != state:
		raise ValueError('Cloud login state contains data outside the permitted scope')
	return payload


def encrypt_state(payload: dict, key: str) -> str:
	validate_payload(payload, payload.get('name', 'main'))
	nonce = os.urandom(12)
	plaintext = json.dumps(payload, separators=(',', ':')).encode('utf-8')
	ciphertext = AESGCM(decode_key(key)).encrypt(nonce, plaintext, AAD)
	return json.dumps(
		{
			'version': 1,
			'nonce': base64.b64encode(nonce).decode('ascii'),
			'data': base64.b64encode(ciphertext).decode('ascii'),
		},
		separators=(',', ':'),
	)


def decrypt_state(encrypted: str, key: str, name: str = 'main') -> dict:
	if len(encrypted) > 1_000_000:
		raise ValueError('Cloud login state is too large')
	envelope = json.loads(encrypted)
	if envelope.get('version') != 1:
		raise ValueError('Unknown cloud encryption envelope version')
	plaintext = AESGCM(decode_key(key)).decrypt(
		base64.b64decode(envelope['nonce'], validate=True),
		base64.b64decode(envelope['data'], validate=True),
		AAD,
	)
	return validate_payload(json.loads(plaintext), name)
