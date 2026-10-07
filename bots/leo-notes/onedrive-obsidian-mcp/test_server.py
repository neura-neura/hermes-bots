import importlib.util
import unittest
from pathlib import Path

SERVER = Path(__file__).with_name('server.py')
spec = importlib.util.spec_from_file_location('obsidian_server_for_tests', SERVER)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class CreatedDateTests(unittest.TestCase):
    def test_adds_creation_date_when_frontmatter_is_absent(self):
        self.assertEqual(
            server.stamp_created_date('A note', '2026-09-29'),
            '---\ncreated: 2026-09-29\n---\n\nA note',
        )

    def test_replaces_generated_created_date_in_existing_frontmatter(self):
        original = '---\ntitle: "Test"\ncreated: 2020-01-01\n---\n\nBody'
        expected = '---\ntitle: "Test"\ncreated: 2026-09-29\n---\n\nBody'
        self.assertEqual(server.stamp_created_date(original, '2026-09-29'), expected)

    def test_adds_created_field_to_frontmatter_without_it(self):
        original = '---\ntitle: "Test"\n---\n\nBody'
        expected = '---\ntitle: "Test"\ncreated: 2026-09-29\n---\n\nBody'
        self.assertEqual(server.stamp_created_date(original, '2026-09-29'), expected)


class SafePathTests(unittest.TestCase):
    def test_safe_is_idempotent_for_vault_relative_and_resolved_paths(self):
        relative = server.safe('Inbox/Prueba')
        self.assertEqual(relative, server.safe(relative))
        self.assertEqual(relative, 'Apps/remotely-save/notes/Inbox/Prueba.md')

    def test_safe_rejects_paths_outside_the_configured_vault(self):
        with self.assertRaises(ValueError):
            server.safe('../outside.md')


if __name__ == '__main__':
    unittest.main()
