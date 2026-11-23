"""Prototype checks; use python -m unittest discover -s this-directory."""
import unittest
from server import analyze


class AnalysisTests(unittest.TestCase):
    def test_module_function_and_helper_calls(self):
        source = 'import studio\ndef helper():\n return studio.nodes.postgres(query="SELECT 1")\ndef operation():\n return helper()\n'
        result = analyze(source)
        self.assertTrue(result['valid'])
        self.assertEqual(result['edges'][0]['to'], 'helper')
        self.assertEqual(result['functions'][0]['calls'][0]['kind'], 'connector')

    def test_shadowed_function_is_not_linked_to_global_definition(self):
        result = analyze('def helper(): pass\ndef run(helper): return helper()')
        self.assertEqual(result['edges'], [])
        self.assertEqual(result['functions'][1]['calls'][0]['kind'], 'custom')

    def test_nested_definition_is_not_claimed_as_outer_execution(self):
        result = analyze('def helper(): pass\ndef run():\n def nested():\n  helper()\n return 1')
        self.assertEqual(result['edges'], [])
        self.assertEqual(result['functions'][1]['custom_scopes'][0]['name'], 'nested')

    def test_parse_never_executes_source(self):
        result = analyze('raise RuntimeError("must not execute")\ndef untouched(): return 1')
        self.assertTrue(result['valid'])
        self.assertEqual(len(result['functions']), 1)

    def test_syntax_error_has_revision_and_no_fabricated_graph(self):
        result = analyze('def broken(')
        self.assertFalse(result['valid'])
        self.assertNotIn('functions', result)
        self.assertEqual(len(result['source_hash']), 64)

    def test_secret_names_only_and_dynamic_callee_is_unresolved(self):
        result = analyze('import studio\ndef run(f):\n value = studio.secret("PG_PASSWORD")\n return f()')
        self.assertEqual(result['functions'][0]['secret_references'], ['PG_PASSWORD'])
        self.assertEqual(result['functions'][0]['calls'][0]['kind'], 'custom')

    def test_routes_require_known_api_binding(self):
        self.assertEqual(analyze('@api.get("/fake")\ndef run(): pass')['functions'][0]['routes'], [])
        result = analyze('from studio import api as web\n@web.get("/actual")\ndef run(): pass')
        self.assertEqual(result['functions'][0]['routes'][0]['path'], '/actual')

    def test_effectful_builtins_remain_custom(self):
        result = analyze('def run(source): return eval(source)')
        self.assertEqual(result['functions'][0]['calls'][0]['kind'], 'custom')

    def test_revision_changes_with_source(self):
        self.assertNotEqual(analyze('def a(): pass')['source_hash'], analyze('def b(): pass')['source_hash'])


if __name__ == '__main__':
    unittest.main()
