import json
import os
import unittest
from unittest.mock import patch

import httpx
from langchain_deepseek import ChatDeepSeek

from backend.app import ai
from backend.app.service import BusinessError


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'TRACKER_AI_ENABLED': 'true', 'DEEPSEEK_API_KEY': 'test-only-key',
                                     'DEEPSEEK_MODEL': 'deepseek-v4-flash'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def invoke(self, content='{"intent":"ignore"}', finish='stop'):
        self.requests = []

        def respond(request):
            self.requests.append(request)
            return httpx.Response(200, json={'id': 'test-completion', 'object': 'chat.completion',
                'created': 0, 'model': 'deepseek-v4-flash', 'choices': [{'index': 0,
                'message': {'role': 'assistant', 'content': content}, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            def model(**kwargs):
                kwargs['http_client'] = client
                return ChatDeepSeek(**kwargs)
            with patch('langchain_deepseek.ChatDeepSeek', side_effect=model):
                return ai.invoke_deepseek('这是需要解析的项目消息')

    def invoke_tools(self, tool_calls, accept):
        self.requests = []
        responses = [
            {'role': 'assistant', 'content': '', 'tool_calls': tool_calls},
            {'role': 'assistant', 'content': 'DONE'},
        ]

        def respond(request):
            self.requests.append(request)
            message = responses.pop(0)
            return httpx.Response(200, json={
                'id': 'test-completion', 'object': 'chat.completion',
                'created': 0, 'model': 'deepseek-v4-flash',
                'choices': [{'index': 0, 'message': message,
                             'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
            })

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            def model(**kwargs):
                kwargs['http_client'] = client
                return ChatDeepSeek(**kwargs)
            with patch('langchain_deepseek.ChatDeepSeek', side_effect=model):
                return ai.invoke_deepseek_tools('这是包含两个项目的消息', accept)

    @staticmethod
    def tool_call(call_id, name, arguments):
        return {'id': call_id, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}

    def test_requires_explicit_deepseek_configuration(self):
        self.assertTrue(ai.configured())
        for name in ('TRACKER_AI_ENABLED', 'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL'):
            with patch.dict(os.environ, {name: ''}):
                self.assertFalse(ai.configured())
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': '', 'DASHSCOPE_API_KEY': 'legacy', 'DASHSCOPE_MODEL': 'legacy'}):
            self.assertFalse(ai.configured())

    def test_native_sdk_sends_json_request_only_to_deepseek(self):
        self.assertEqual(self.invoke(), '{"intent":"ignore"}')
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://api.deepseek.com/chat/completions')
        self.assertEqual(request.headers['authorization'], 'Bearer test-only-key')
        body = json.loads(request.content)
        self.assertEqual(body['model'], 'deepseek-v4-flash')
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['thinking'], {'type': 'disabled'})
        self.assertEqual(body['messages'][0]['role'], 'system')
        self.assertIn('JSON', body['messages'][0]['content'])
        self.assertNotIn('test-only-key', body['messages'][0]['content'])
        self.assertNotIn('tools', body)

    def test_native_sdk_emits_multiple_business_tool_calls(self):
        accepted = []
        calls = [
            self.tool_call('call-1', 'record_project_item', {'data': {
                'project_name': '北斗创新中心', 'text': '9月15日验收'}}),
            self.tool_call('call-2', 'record_project_item', {'data': {
                'project_name': '美国宠物医院', 'text': '9月11日交付'}}),
        ]

        result = self.invoke_tools(calls, lambda name, arguments: (
            accepted.append((name, arguments)) or {'accepted': True}))

        self.assertEqual([arguments['data']['project_name'] for _, arguments in accepted],
                         ['北斗创新中心', '美国宠物医院'])
        self.assertEqual(len(result), 2)
        first_body = json.loads(self.requests[0].content)
        self.assertIn('tools', first_body)
        self.assertNotIn('response_format', first_body)
        second_body = json.loads(self.requests[1].content)
        self.assertEqual([message['role'] for message in second_body['messages'][-3:]],
                         ['assistant', 'tool', 'tool'])

    def test_native_sdk_accepts_more_than_twenty_tool_calls(self):
        calls = [self.tool_call(f'call-{index}', 'record_project_item', {
            'data': {'project_name': f'项目{index}', 'text': '记录安排'}
        }) for index in range(21)]
        accepted = []

        result = self.invoke_tools(calls, lambda name, arguments: (
            accepted.append(arguments['data']['project_name']) or {'accepted': True}))

        self.assertEqual(len(result), 21)
        self.assertEqual(accepted, [f'项目{index}' for index in range(21)])

    def test_native_sdk_rejects_unregistered_tool(self):
        calls = [self.tool_call('call-unknown', 'drop_database', {})]

        with self.assertRaises(BusinessError) as caught:
            self.invoke_tools(calls, lambda name, arguments: {'accepted': True})

        self.assertIn('未知', caught.exception.message)

    def test_empty_or_truncated_content_is_rejected(self):
        for content, finish in (('', 'stop'), ('{"intent":"ignore"}', 'length')):
            with self.subTest(finish=finish), self.assertRaises(BusinessError):
                self.invoke(content, finish)
