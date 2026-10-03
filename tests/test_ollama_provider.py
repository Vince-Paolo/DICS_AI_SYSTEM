import importlib
import json
import os
from unittest.mock import patch

from ai import decision_support


def test_provider_defaults_to_ollama(monkeypatch):
    monkeypatch.setattr(decision_support.os, 'getenv', lambda key, default=None: default)

    importlib.reload(decision_support)

    assert decision_support.AI_PROVIDER == 'ollama'


def test_ollama_adapter_sends_json_chat_request():
    response = {'message': {'content': '{"score": 10}'}}
    with patch.dict(os.environ, {'OLLAMA_BASE_URL': 'http://localhost:11434/'}), \
         patch('ai.decision_support.urllib.request.urlopen') as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()

        result = decision_support._call_ollama('system prompt', 'user prompt', None, 'llama3.2:latest')

    request = urlopen.call_args.args[0]
    payload = json.loads(request.data.decode('utf-8'))
    assert request.full_url == 'http://localhost:11434/api/chat'
    assert payload['model'] == 'llama3.2:latest'
    assert payload['format'] == 'json'
    assert payload['stream'] is False
    assert payload['messages'] == [
        {'role': 'system', 'content': 'system prompt'},
        {'role': 'user', 'content': 'user prompt'},
    ]
    assert result == '{"score": 10}'


def test_parsed_risk_level_follows_score_band():
    result = decision_support._parse_ai_response(
        json.dumps({'score': 35, 'level': 'Low'}),
        'flood',
    )

    assert result['level'] == 'Moderate'


def test_parsed_response_preserves_filipino_message():
    result = decision_support._parse_ai_response(
        json.dumps({
            'score': 62,
            'level': 'High',
            'message': 'Flood risk is high near the river.',
            'message_fil': 'Mataas ang panganib ng baha malapit sa ilog.',
        }),
        'flood',
    )

    assert result['message'] == 'Flood risk is high near the river.'
    assert result['message_fil'] == 'Mataas ang panganib ng baha malapit sa ilog.'


def test_insufficient_response_uses_rainfall_humidity_fallback():
    result = decision_support._parse_ai_response(
        json.dumps({
            'score': 0,
            'level': 'Insufficient Data',
            'message': 'The model could not assess the hazard.',
        }),
        'flood',
        rainfall_mm=50,
        humidity_pct=90,
    )

    assert result['score'] == 30
    assert result['level'] == 'Moderate'
    assert 'conservative rainfall/humidity heuristic' in result['message']


def test_insufficient_response_preserves_higher_model_score():
    result = decision_support._parse_ai_response(
        json.dumps({'score': 42, 'level': 'INSUFFICIENT_DATA'}),
        'flood',
        rainfall_mm=20,
        humidity_pct=85,
    )

    assert result['score'] == 42
    assert result['level'] == 'Moderate'


def test_insufficient_response_is_honored_when_a_reading_is_missing():
    result = decision_support._parse_ai_response(
        json.dumps({'score': 0, 'level': 'Insufficient Data'}),
        'flood',
        rainfall_mm=20,
        humidity_pct=None,
    )

    assert result['score'] == 0
    assert result['level'] == 'INSUFFICIENT_DATA'


def test_ollama_assessment_does_not_require_api_key(monkeypatch):
    monkeypatch.setattr(decision_support, 'AI_PROVIDER', 'ollama')
    monkeypatch.delenv('OLLAMA_API_KEY', raising=False)
    monkeypatch.setenv('OLLAMA_MODEL', 'llama3.2:latest')
    monkeypatch.setitem(
        decision_support._ADAPTERS,
        'ollama',
        lambda system_prompt, user_prompt, api_key, model: json.dumps({
            'score': 30,
            'confidence': 80,
            'level': 'Moderate',
            'message': 'Moderate risk based on test inputs.',
        }),
    )

    result = decision_support.assess_hazard(
        'flood', rainfall_mm=20, river_level_m=None, humidity_pct=85,
        population_density=1000,
    )

    assert result['level'] == 'Moderate'
    assert result['degraded'] is False
    assert result['provider'] == 'ollama'
    assert result['model'] == 'llama3.2:latest'


def test_assessment_applies_fallback_to_insufficient_model_response(monkeypatch):
    monkeypatch.setattr(decision_support, 'AI_PROVIDER', 'ollama')
    monkeypatch.setenv('OLLAMA_MODEL', 'llama3.2:latest')
    monkeypatch.setitem(
        decision_support._ADAPTERS,
        'ollama',
        lambda system_prompt, user_prompt, api_key, model: json.dumps({
            'score': 0,
            'level': 'Insufficient Data',
            'message': 'River gauge data is unavailable.',
        }),
    )

    result = decision_support.assess_hazard(
        'flood', rainfall_mm=50, river_level_m=None, humidity_pct=90,
        population_density=1000,
    )

    assert result['score'] == 30
    assert result['level'] == 'Moderate'
    assert 'rainfall/humidity heuristic' in result['message']