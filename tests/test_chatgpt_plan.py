"""Check the ChatGPT plan adapter with fake streams and no network access."""

import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from vaurien_atlas.chatgpt_plan import PlanClient, _host_id


class FakeStream:
    """Simulate the SDK's streamed response context manager."""
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *args):
        return False


class PlanAdapterTests(unittest.TestCase):
    def test_host_identity_is_stable_without_storing_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOCALAPPDATA": directory}):
            first = _host_id()
            self.assertEqual(_host_id(), first)
            self.assertTrue(first.startswith("urn:uuid:"))

    def test_streaming_request_and_usage(self):
        requests = []
        usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        events = [SimpleNamespace(type="response.output_text.delta", delta="{"),
                  SimpleNamespace(type="response.output_text.delta", delta="}"),
                  SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage))]
        def create(**kwargs):
            requests.append(kwargs)
            return FakeStream(events)
        adapter = PlanClient(SimpleNamespace(responses=SimpleNamespace(create=create)))
        result = adapter.responses.create(model="test", instructions="instructions", input="data",
                                          max_output_tokens=4000, store=False, text={"format": {}})
        self.assertEqual(result.output_text, "{}")
        self.assertIs(result.usage, usage)
        self.assertEqual(requests[0]["input"], [{"role": "user", "content": "data"}])
        self.assertTrue(requests[0]["stream"])
        self.assertNotIn("max_output_tokens", requests[0])

    def test_failure_incomplete_and_oversized_output(self):
        for events in ([SimpleNamespace(type="response.failed")], [],
                       [SimpleNamespace(type="response.output_text.delta", delta="x" * 100_001)]):
            client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: FakeStream(events)))
            with self.subTest(events=len(events)), self.assertRaises(ValueError):
                PlanClient(client).create(model="test", instructions="", input="x", max_output_tokens=10,
                                          store=False, text={})

    def test_failure_displays_only_safe_event_type(self):
        # The provider error might contain source text or secrets; do not echo it.
        event = SimpleNamespace(type="response.failed", response=SimpleNamespace(error="private data"))
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: FakeStream([event])))
        with self.assertRaisesRegex(ValueError, "stream event: response.failed") as error:
            PlanClient(client).create(model="test", instructions="", input="x", max_output_tokens=10,
                                      store=False, text={})
        self.assertNotIn("private data", str(error.exception))
