import unittest

from gmail_organizer import classify_message, parse_command, search_query


class CommandTests(unittest.TestCase):
    def test_parses_clean_sender_command(self):
        self.assertEqual(
            parse_command("límpiame este email newsletters@example.com"),
            ("clean", "newsletters@example.com"),
        )

    def test_parses_organize_sender_command(self):
        self.assertEqual(
            parse_command("organízame este otro alerts@example.com"),
            ("organize", "alerts@example.com"),
        )

    def test_rejects_unknown_command(self):
        with self.assertRaises(ValueError):
            parse_command("borra todo")


class PolicyTests(unittest.TestCase):
    def test_financial_message_is_important(self):
        result = classify_message({"subject": "Pago autorizado", "sender": "service@paypal.com"})
        self.assertEqual(result["label"], "Importante")
        self.assertFalse(result["archive"])

    def test_newsletter_is_archive_candidate(self):
        result = classify_message({"subject": "Weekly newsletter", "sender": "news@example.com"})
        self.assertEqual(result["label"], "Newsletters")
        self.assertTrue(result["archive"])

    def test_ieu_message_is_academic(self):
        result = classify_message({"subject": "Universidad IEU - Segunda entrega", "sender": "no-reply@example.com"})
        self.assertEqual(result["label"], "Académico")
        self.assertFalse(result["archive"])

    def test_search_query_uses_sender_safely(self):
        self.assertEqual(search_query("a+b@example.com"), 'from:(a+b@example.com)')


if __name__ == "__main__":
    unittest.main()
