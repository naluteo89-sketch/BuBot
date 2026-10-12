import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import gpt_api
from bubot.documents import read_corpus, export_documents


class DocumentTests(unittest.TestCase):
    def test_real_csv_document_counts_metadata_and_missing(self):
        corpus = read_corpus()
        self.assertEqual((corpus.region_count, len(corpus.months), corpus.missing_count), (245, 176, 4126))
        self.assertEqual(len(corpus.documents), 43365)
        self.assertEqual(len({d.id for d in corpus.documents}), 43365)
        first = corpus.documents[0]
        self.assertIn("264,474천원", first.text)
        self.assertEqual(first.metadata["price_thousand_krw"], 264474)
        self.assertEqual(first.metadata["source_row"], 4)
        self.assertEqual(first.metadata["source_sha256"], corpus.source_hash)
        missing = [d for d in corpus.documents if d.metadata.get("missing")]
        self.assertEqual(len(missing), 4126)
        self.assertTrue(all("price_thousand_krw" not in d.metadata for d in missing))
        self.assertTrue(all("데이터 없음" in d.text for d in missing))

    def test_api_response_reordering_and_dimensions(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.embeddings.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=1, embedding=[0.1, 1.0, 0.2]),
            SimpleNamespace(index=0, embedding=[1.0, 0.2, 0.1])])
        with patch("gpt_api.create_client", return_value=client), patch("gpt_api.EMBEDDING_DIMENSIONS", 3):
            result = gpt_api.embed_texts(["서울", "부산"])
            self.assertEqual(result[0], [1.0, 0.2, 0.1])
            self.assertEqual(client.embeddings.create.call_args.kwargs["encoding_format"], "float")
            client.embeddings.create.return_value.data[0].embedding = [1.0]
            with self.assertRaises(ValueError):
                gpt_api.embed_texts(["서울", "부산"])

    def test_no_key_fails_without_network(self):
        with patch("gpt_api.KEY_FILE", Path("missing-test-key.py")), patch("gpt_api.API_KEY", ""), patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "API_KEY"):
                gpt_api.create_client()


class CsvExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.csv = self.root / "data.csv"
        with self.csv.open("w", encoding="cp949", newline="") as stream:
            csv.writer(stream).writerows([
                ["No", "지역", "지역", "지역", "지역", "2026년 7월", "2026년 8월"],
                ["No", "지역", "지역", "지역", "지역", "가격", "가격"],
                ["No", "지역", "지역", "지역", "지역", "천원", "천원"],
                ["8", "서울", "서울", "서울", "서울", '"1,000"', '"1,100"'],
                ["9", "부산", "부산", "부산", "부산", "", '"800"']])

    def tearDown(self):
        self.temp.cleanup()

    def test_export_and_bad_csv(self):
        output = self.root / "docs.jsonl"
        export_documents(read_corpus(self.csv), output)
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 6)
        self.csv.write_bytes(self.csv.read_bytes().replace(b'1,100', b'NaN'))
        with self.assertRaises(ValueError):
            read_corpus(self.csv)


if __name__ == "__main__":
    unittest.main()
