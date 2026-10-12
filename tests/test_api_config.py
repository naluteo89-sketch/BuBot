import json
import re
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gpt_api


class APIConfigTests(unittest.TestCase):
    def test_file_key_reaches_sdk_without_network(self):
        with patch.object(gpt_api, "API_KEY", "  test-not-a-real-key  "), patch("openai.OpenAI") as sdk:
            gpt_api.create_client()
            self.assertEqual(sdk.call_args.kwargs["api_key"], "test-not-a-real-key")
            status = gpt_api.check_configuration()
            self.assertTrue(status["키 인식"])
            self.assertNotIn("test-not-a-real-key", json.dumps(status))

    def test_whitespace_does_not_hide_environment_key(self):
        with patch.object(gpt_api, "API_KEY", "  "), patch.dict(os.environ, {"OPENAI_API_KEY": "test-env"}, clear=True):
            self.assertEqual(gpt_api.resolve_api_key(), ("test-env", "환경변수 OPENAI_API_KEY"))

    def test_previous_environment_name_supported(self):
        with patch.object(gpt_api, "API_KEY", ""), patch.dict(os.environ, {"BUBOT_API_KEY": "test-old"}, clear=True):
            self.assertEqual(gpt_api.resolve_api_key()[0], "test-old")

    def test_saved_file_loaded_in_new_process(self):
        # 사용자 설정은 수정하지 않고 별도 파일에 가짜 키를 저장하여 재현합니다.
        with tempfile.TemporaryDirectory() as directory:
            source = Path(gpt_api.__file__).read_text(encoding="utf-8")
            source = re.sub(r'^API_KEY\s*=.*$', 'API_KEY = "test-saved-key"', source, count=1, flags=re.MULTILINE)
            Path(directory, "gpt_api.py").write_text(source, encoding="utf-8")
            process = subprocess.run([sys.executable, "-c",
                "import gpt_api; assert gpt_api.resolve_api_key()[0]=='test-saved-key'; print('OK')"],
                cwd=directory, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout.strip(), "OK")
