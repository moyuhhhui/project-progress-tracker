import unittest

from backend.app.ai_tools import action_from_tool_call, business_tool_schemas
from backend.app.service import BusinessError


class AIToolTests(unittest.TestCase):
    def test_record_tool_uses_record_item_business_fields(self):
        tool = next(item for item in business_tool_schemas()
                    if item['function']['name'] == 'record_project_item')
        parameters = tool['function']['parameters']
        data = parameters['properties']['data']

        self.assertFalse(parameters['additionalProperties'])
        self.assertFalse(data['additionalProperties'])
        self.assertIn('owner_assignments', data['properties'])
        self.assertIn('items', data['properties'])

    def test_record_tool_call_becomes_existing_action(self):
        action = action_from_tool_call('record_project_item', {
            'data': {'project_name': '美国宠物医院', 'text': '9月11日交付'}
        }, {'4'})

        self.assertEqual(action.intent, 'record_item')
        self.assertEqual(action.data, {
            'text': '9月11日交付',
            'project_name': '美国宠物医院',
        })

    def test_tool_call_rejects_unknown_project_id(self):
        with self.assertRaises(BusinessError) as caught:
            action_from_tool_call('edit_project', {
                'project_id': '999',
                'data': {'name': '越权修改', 'reason': '测试'},
            }, {'4'})

        self.assertIn('不可用', caught.exception.message)

    def test_tool_call_rejects_null_instead_of_treating_it_as_missing(self):
        with self.assertRaises(BusinessError) as caught:
            action_from_tool_call('record_project_item', {
                'data': {'project_name': '北斗创新中心', 'text': '完成验收', 'due_date': None}
            }, set())

        self.assertIn('null', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
