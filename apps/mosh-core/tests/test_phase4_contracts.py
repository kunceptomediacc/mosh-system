import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class Phase4ContractTests(unittest.TestCase):
    def _schema(self, name: str) -> dict:
        path = ROOT / "bridge" / "contracts" / "v1" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def test_memory_contract_is_closed_and_has_governed_lifecycle(self):
        schema = self._schema("memory-record.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["status"]["enum"],
            ["candidate", "validated", "approved", "rejected", "expired"],
        )
        self.assertEqual(schema["properties"]["scope"]["enum"], ["task", "project", "mosh"])
        self.assertIn("content_sha256", schema["required"])
        self.assertIn("source", schema["required"])
        self.assertNotIn("content", schema["properties"])
        self.assertNotIn("credential", json.dumps(schema).lower())

    def test_skill_contract_requires_evidence_and_digest(self):
        schema = self._schema("skill-candidate.schema.json")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("procedure_sha256", schema["required"])
        self.assertIn("evidence_ids", schema["required"])
        self.assertGreaterEqual(schema["properties"]["evidence_ids"]["minItems"], 1)
        self.assertTrue(re.fullmatch(r"\^[^$]+\$", schema["properties"]["procedure_ref"]["pattern"]))
        self.assertNotIn("procedure_content", schema["properties"])

    def test_contracts_are_versioned_and_use_json_schema_2020_12(self):
        for name in ("memory-record.schema.json", "skill-candidate.schema.json"):
            schema = self._schema(name)
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertIn("/contracts/v1/", schema["$id"])


if __name__ == "__main__":
    unittest.main()
