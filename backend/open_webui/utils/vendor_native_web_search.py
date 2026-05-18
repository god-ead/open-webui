import ast
import codecs
import copy
import json
import logging

import aiohttp
from fastapi.responses import StreamingResponse

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT
from open_webui.socket.main import get_event_call
from open_webui.utils.tools import get_updated_tool_function

log = logging.getLogger(__name__)

KIMI_MAX_TOOL_ROUNDS = 50
KIMI_WEB_SEARCH_TOOL = {
    'type': 'builtin_function',
    'function': {'name': '$web_search'},
}
SEARCH_TOOL_NAMES = {'$web_search', 'search_web', 'web_search'}
SEARCH_TOOL_TYPES = {'web_search', 'web_search_preview'}


def get_api_web_search_config(model: dict, features: dict, resolved_model_id: str | None = None) -> dict | None:
    del resolved_model_id

    if not features.get('api_web_search'):
        return None

    config = model.get('info', {}).get('meta', {}).get('api_web_search') or {}
    provider = (config.get('provider') or '').lower()

    if not config.get('enabled'):
        return None

    if provider not in {'kimi', 'moonshot'}:
        return None

    return {
        'provider': 'kimi',
        'enabled': bool(features.get('web_search')),
    }


def _tool_name(tool: dict) -> str:
    if not isinstance(tool, dict):
        return ''

    function = tool.get('function') or {}
    return function.get('name', '') or tool.get('name', '')


def _is_search_tool(tool: dict) -> bool:
    if not isinstance(tool, dict):
        return False

    return tool.get('type') in SEARCH_TOOL_TYPES or _tool_name(tool) in SEARCH_TOOL_NAMES


def _is_search_tool_choice(tool_choice) -> bool:
    if not isinstance(tool_choice, dict):
        return False

    if tool_choice.get('type') in SEARCH_TOOL_TYPES:
        return True

    function = tool_choice.get('function') or {}
    return function.get('name', '') in SEARCH_TOOL_NAMES


def _build_request_payload(payload: dict) -> dict:
    request_payload = copy.deepcopy(payload)
    tools = request_payload.get('tools')

    if isinstance(tools, list):
        request_payload['tools'] = [copy.deepcopy(tool) for tool in tools if not _is_search_tool(tool)]
    else:
        request_payload['tools'] = []

    if not any(_tool_name(tool) == '$web_search' for tool in request_payload['tools']):
        request_payload['tools'].append(copy.deepcopy(KIMI_WEB_SEARCH_TOOL))

    if _is_search_tool_choice(request_payload.get('tool_choice')):
        request_payload['tool_choice'] = copy.deepcopy(KIMI_WEB_SEARCH_TOOL)

    request_payload['thinking'] = {'type': 'disabled'}
    return request_payload


async def _request_json(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    cookies: dict,
    body: dict,
) -> dict:
    async with session.post(
        url,
        json=body,
        headers=headers,
        cookies=cookies,
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    ) as response:
        try:
            payload = await response.json()
        except Exception:
            payload = await response.text()

        if response.status >= 400:
            raise RuntimeError(f'Kimi API 请求失败 {response.status}: {payload}')

        return payload


async def _request_stream(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    cookies: dict,
    body: dict,
) -> aiohttp.ClientResponse:
    response = await session.post(
        url,
        json=body,
        headers=headers,
        cookies=cookies,
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    )

    if response.status >= 400:
        try:
            payload = await response.json()
        except Exception:
            payload = await response.text()

        response.release()
        raise RuntimeError(f'Kimi API 请求失败 {response.status}: {payload}')

    return response


def _append_delta_tool_calls(accumulated_tool_calls: list[dict], delta_tool_calls: list[dict]) -> None:
    for delta_tool_call in delta_tool_calls:
        index = delta_tool_call.get('index', 0)
        while len(accumulated_tool_calls) <= index:
            accumulated_tool_calls.append(
                {
                    'id': '',
                    'type': 'function',
                    'function': {'name': '', 'arguments': ''},
                }
            )

        tool_call = accumulated_tool_calls[index]
        if delta_tool_call.get('id'):
            tool_call['id'] = delta_tool_call['id']
        if delta_tool_call.get('type'):
            tool_call['type'] = delta_tool_call['type']

        delta_function = delta_tool_call.get('function') or {}
        function = tool_call.setdefault('function', {})
        if delta_function.get('name'):
            function['name'] = delta_function['name']
        if delta_function.get('arguments'):
            function['arguments'] = function.get('arguments', '') + delta_function['arguments']


def _normalize_search_tool_arguments(arguments: str) -> str:
    if not arguments or not arguments.strip():
        return '{}'

    try:
        return json.dumps(json.loads(arguments), ensure_ascii=False)
    except Exception:
        return arguments


def _parse_tool_arguments(tool_args: str, tool_function_name: str) -> dict:
    if not tool_args or not tool_args.strip():
        return {}

    try:
        parsed = ast.literal_eval(tool_args)
    except Exception:
        try:
            parsed = json.loads(tool_args)
        except Exception as exc:
            raise RuntimeError(
                f'Error: Tool call arguments could not be parsed for `{tool_function_name}`.'
            ) from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f'Error: Tool call arguments for `{tool_function_name}` must be an object.')

    return parsed


def _normalize_tool_result(tool_result) -> str:
    if tool_result is None:
        return ''
    if isinstance(tool_result, str):
        return tool_result
    if isinstance(tool_result, (dict, list)):
        return json.dumps(tool_result, ensure_ascii=False)
    if isinstance(tool_result, tuple) and tool_result:
        return _normalize_tool_result(tool_result[0])
    return str(tool_result)


def _build_assistant_tool_message(
    content: str | None,
    tool_calls: list[dict],
    reasoning_content: str | None = None,
) -> dict:
    message = {'role': 'assistant', 'tool_calls': tool_calls}
    if content is not None:
        message['content'] = content
    if reasoning_content:
        message['reasoning_content'] = reasoning_content
    return message


async def _execute_openwebui_tool(tool_call: dict, available_tools: dict, metadata: dict) -> str:
    tool_name = tool_call.get('function', {}).get('name', '')
    tool_args = tool_call.get('function', {}).get('arguments', '{}')

    if tool_name not in available_tools:
        return f'Error: Tool `{tool_name}` is not available in OpenWebUI.'

    tool = available_tools[tool_name]
    spec = tool.get('spec', {})
    tool_params = _parse_tool_arguments(tool_args, tool_name)
    allowed_params = spec.get('parameters', {}).get('properties', {}).keys()
    tool_params = {key: value for key, value in tool_params.items() if key in allowed_params}
    tool_call.setdefault('function', {})['arguments'] = json.dumps(tool_params, ensure_ascii=False)

    try:
        if tool.get('direct', False):
            event_caller = get_event_call(metadata)
            tool_result = await event_caller(
                {
                    'type': 'execute:tool',
                    'data': {
                        'id': tool_call.get('id', ''),
                        'name': tool_name,
                        'params': tool_params,
                        'server': tool.get('server', {}),
                        'session_id': metadata.get('session_id'),
                    },
                }
            )
        else:
            tool_function = get_updated_tool_function(
                function=tool['callable'],
                extra_params={
                    '__messages__': metadata.get('__messages__', []),
                    '__files__': metadata.get('files', []),
                },
            )
            tool_result = await tool_function(**tool_params)
    except Exception as exc:
        tool_result = str(exc)

    return _normalize_tool_result(tool_result)


async def _append_tool_results(
    request_payload: dict,
    assistant_message: dict,
    tool_calls: list[dict],
    available_tools: dict,
    metadata: dict,
) -> None:
    request_payload.setdefault('messages', []).append(assistant_message)

    for tool_call in tool_calls:
        tool_name = _tool_name(tool_call)
        if tool_name in SEARCH_TOOL_NAMES:
            tool_content = _normalize_search_tool_arguments(tool_call.get('function', {}).get('arguments', '{}'))
        else:
            tool_content = await _execute_openwebui_tool(tool_call, available_tools, metadata)

        request_payload['messages'].append(
            {
                'role': 'tool',
                'tool_call_id': tool_call.get('id', ''),
                'name': tool_name,
                'content': tool_content,
            }
        )


def _extract_response_state(response: dict) -> tuple[str | None, dict, list[dict]]:
    choice = (response.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    return choice.get('finish_reason'), message, message.get('tool_calls') or []


async def _iter_sse_data(response: aiohttp.ClientResponse):
    buffer = ''
    decoder = codecs.getincrementaldecoder('utf-8')()

    async for chunk in response.content.iter_any():
        buffer += decoder.decode(chunk)
        buffer = buffer.replace('\r\n', '\n').replace('\r', '\n')

        while '\n\n' in buffer:
            raw_event, buffer = buffer.split('\n\n', 1)
            data_lines = []

            for line in raw_event.splitlines():
                if line.startswith('data:'):
                    data_lines.append(line[5:].lstrip())

            if data_lines:
                yield '\n'.join(data_lines)

    buffer += decoder.decode(b'', final=True)
    buffer = buffer.replace('\r\n', '\n').replace('\r', '\n')

    if buffer.strip():
        data_lines = []
        for line in buffer.splitlines():
            if line.startswith('data:'):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            yield '\n'.join(data_lines)


async def _execute_non_stream_rounds(
    session: aiohttp.ClientSession,
    request_url: str,
    headers: dict,
    cookies: dict,
    request_payload: dict,
    available_tools: dict,
    metadata: dict,
):
    for _ in range(KIMI_MAX_TOOL_ROUNDS):
        response = await _request_json(session, request_url, headers, cookies, {**request_payload, 'stream': False})
        finish_reason, message, tool_calls = _extract_response_state(response)

        if finish_reason != 'tool_calls' or not tool_calls:
            return response

        metadata['__messages__'] = request_payload.get('messages', [])
        await _append_tool_results(
            request_payload,
            _build_assistant_tool_message(
                content=message.get('content'),
                tool_calls=tool_calls,
                reasoning_content=message.get('reasoning_content'),
            ),
            tool_calls,
            available_tools,
            metadata,
        )

    raise RuntimeError('Kimi API 联网搜索未在限定轮次内返回最终结果')


async def _stream_kimi_round(
    session: aiohttp.ClientSession,
    request_url: str,
    headers: dict,
    cookies: dict,
    body: dict,
):
    response = await _request_stream(session, request_url, headers, cookies, body)
    state = {
        'assistant_content': '',
        'reasoning_content': '',
        'tool_calls': [],
        'finish_reason': None,
    }

    async def event_generator():
        try:
            async for data in _iter_sse_data(response):
                if data == '[DONE]':
                    break

                try:
                    chunk = json.loads(data)
                except Exception:
                    continue

                choice = (chunk.get('choices') or [{}])[0]
                delta = choice.get('delta') or {}

                if delta.get('content'):
                    state['assistant_content'] += delta['content']
                    yield f'data: {json.dumps(chunk, ensure_ascii=False)}\n\n'

                reasoning_content = delta.get('reasoning_content') or delta.get('reasoning')
                if reasoning_content:
                    state['reasoning_content'] += reasoning_content

                delta_tool_calls = delta.get('tool_calls') or []
                if delta_tool_calls:
                    _append_delta_tool_calls(state['tool_calls'], delta_tool_calls)

                if choice.get('finish_reason') is not None:
                    state['finish_reason'] = choice.get('finish_reason')
                    if state['finish_reason'] != 'tool_calls':
                        yield f'data: {json.dumps(chunk, ensure_ascii=False)}\n\n'
        finally:
            response.release()

    return event_generator(), state


def _build_error_chunk(message: str) -> str:
    return (
        'data: '
        + json.dumps(
            {
                'error': {
                    'message': message,
                    'type': 'api_web_search_error',
                }
            },
            ensure_ascii=False,
        )
        + '\n\n'
    )


async def execute_kimi_api_web_search(
    base_url: str,
    headers: dict,
    cookies: dict,
    payload: dict,
    metadata: dict,
):
    request_payload = _build_request_payload(payload)
    request_url = f'{base_url.rstrip("/")}/chat/completions'
    available_tools = (metadata or {}).get('tools', {})
    runtime_metadata = {
        **(metadata or {}),
        '__messages__': request_payload.get('messages', []),
    }
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
    session = aiohttp.ClientSession(trust_env=True, timeout=timeout)

    if not payload.get('stream'):
        async with session:
            return await _execute_non_stream_rounds(
                session=session,
                request_url=request_url,
                headers=headers,
                cookies=cookies,
                request_payload=request_payload,
                available_tools=available_tools,
                metadata=runtime_metadata,
            )

    async def event_generator():
        try:
            for _ in range(KIMI_MAX_TOOL_ROUNDS):
                round_generator, state = await _stream_kimi_round(
                    session=session,
                    request_url=request_url,
                    headers=headers,
                    cookies=cookies,
                    body={**request_payload, 'stream': True},
                )

                async for event in round_generator:
                    yield event

                if state['finish_reason'] != 'tool_calls' or not state['tool_calls']:
                    yield 'data: [DONE]\n\n'
                    return

                runtime_metadata['__messages__'] = request_payload.get('messages', [])
                await _append_tool_results(
                    request_payload,
                    _build_assistant_tool_message(
                        content=state['assistant_content'],
                        tool_calls=state['tool_calls'],
                        reasoning_content=state['reasoning_content'],
                    ),
                    state['tool_calls'],
                    available_tools,
                    runtime_metadata,
                )

            raise RuntimeError('联网搜索异常，请您联系管理员')
        except Exception as exc:
            log.exception('联网搜索失败，请您联系管理员')
            yield _build_error_chunk(str(exc))
        finally:
            await session.close()

    return StreamingResponse(event_generator(), media_type='text/event-stream')
