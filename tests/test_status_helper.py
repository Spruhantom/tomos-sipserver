import runpy
import unittest
from pathlib import Path
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tomos-sipserver-status"
MODULE = runpy.run_path(str(SCRIPT))
class StatusParserTests(unittest.TestCase):
    def test_parses_contacts_without_exposing_addresses_or_hashes(self):
        output = """Contact:  100/sip:100@192.168.1.20:5060;ob  0f00abcd  Avail        12.345
Contact:  101/sip:101@192.168.1.21:5060;ob  0f00abce  Unavail      nan
Contact:  invalid/sip:invalid@192.168.1.22  0f00abcf  Avail        2.000
"""
        self.assertEqual(MODULE["parse_contacts"](output), [{"extension":"100","status":"Avail"},{"extension":"101","status":"Unavail"}])
    def test_empty_contact_list(self):
        self.assertEqual(MODULE["parse_contacts"]("No objects found.\n"), [])
if __name__ == "__main__":
    unittest.main()
