"""Offline checks for the shipped recruitment skills and fictional demo inputs.

Run with the packaged interpreter; no model, API key or extra packages required.
These checks validate packaging and data invariants, not LLM answer quality.
"""
import json
from pathlib import Path
import re
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RECRUIT = Path(__file__).resolve().parents[2] / "recruit"
SKILLS = {"recruit-jd", "recruit-screen", "recruit-interview", "recruit-qa"}


class RecruitSkillsTests(unittest.TestCase):
    def test_four_discoverable_skills(self):
        """Names agree with folders and frontmatter is unambiguous without YAML deps."""
        paths = list((RECRUIT / "skills").glob("*/SKILL.md"))
        self.assertEqual({p.parent.name for p in paths}, SKILLS)
        descriptions = set()
        for path in paths:
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"))
                front, body = text[4:].split("\n---\n", 1)
                names = re.findall(r"^name: (.+)$", front, re.M)
                self.assertEqual(names, [path.parent.name])
                values = re.findall(r"^description: (.+)$", front, re.M)
                self.assertEqual(len(values), 1)
                # Our descriptions are JSON-quoted YAML strings, supported by Hermes.
                description = json.loads(values[0])
                self.assertTrue(10 <= len(description) <= 1024)
                descriptions.add(description)
                self.assertGreater(len(body.strip()), 100)
                self.assertNotRegex(text, r"\{\{#|\[TODO:|/home/|[A-Z]:\\")
                self.assertFalse((path.parent / "scripts").exists())
        self.assertEqual(len(descriptions), 4)

    def test_scoring_dimensions_sum_to_100(self):
        """Changing the rubric must keep six dimensions and a genuine 100 point total."""
        text = (RECRUIT / "skills/recruit-screen/SKILL.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| ([^|]+) \| (\d+) \| ([^|]+) \|$", text, re.M)
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({name for name, _, _ in rows}), 6)
        self.assertEqual(sum(int(score) for _, score, _ in rows), 100)
        self.assertTrue(all(int(score) > 0 and evidence.strip() for _, score, evidence in rows))

    def test_demo_jd_agrees_with_company_terms(self):
        company = (RECRUIT / "company-info.md").read_text(encoding="utf-8")
        jd = (RECRUIT / "default-jd.md").read_text(encoding="utf-8")
        for term in ("青禾智选科技有限公司", "8K–13K", "13 薪", "9:30", "6:30", "双休", "12%", "虚构"):
            with self.subTest(term=term):
                self.assertIn(term, company)
                self.assertIn(term, jd)
        self.assertIn("hr@qinghe-demo.example", company)

    def test_all_five_demo_files_have_readable_text_fallbacks(self):
        root = RECRUIT / "demo-resumes"
        originals = sorted(p for p in root.iterdir() if p.suffix in {".pdf", ".docx"} and p.name[:2] in {"01", "02", "03", "04", "05"})
        texts = sorted((root / "text").glob("0[1-5]_*.txt"))
        self.assertEqual(len(originals), 5)
        self.assertEqual({p.stem for p in originals}, {p.stem for p in texts})
        self.assertEqual([p.suffix for p in originals].count(".pdf"), 3)
        for path in originals:
            with self.subTest(resume=path.name):
                text = (root / "text" / (path.stem + ".txt")).read_text(encoding="utf-8")
                self.assertGreater(len(text), 200)
                self.assertIn(path.stem.split("_")[1], text)
                self.assertIn("虚构", text)
                self.assertNotIn("\ufffd", text)
                emails = re.findall(r"[\w.+-]+@([\w.-]+)", text)
                self.assertTrue(emails)
                self.assertTrue(all(domain == "example.com" for domain in emails))
                phones = re.findall(r"1\d{2}-\d{4}-\d{4}", text)
                self.assertTrue(phones)
                self.assertTrue(all("-0000-" in phone for phone in phones))
                if path.suffix == ".pdf":
                    self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
                    self.assertIn(b"%%EOF", path.read_bytes()[-1024:])
                else:
                    with zipfile.ZipFile(path) as archive:
                        self.assertIsNone(archive.testzip())
                        doc = ET.fromstring(archive.read("word/document.xml"))
                    self.assertIn(path.stem.split("_")[1], "".join(doc.itertext()))


if __name__ == "__main__":
    unittest.main()
