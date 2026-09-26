import ctypes
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rfmapping_viewer import recent_documents


class RecentDocumentsTests(unittest.TestCase):
    def test_non_macos_does_not_load_appkit_or_persist_a_second_history(self) -> None:
        with (
            mock.patch.object(recent_documents.sys, "platform", "linux"),
            mock.patch.object(recent_documents, "_mac_recent_documents") as native,
        ):
            self.assertEqual(recent_documents.list_recent_documents(), [])
            recent_documents.record_recent_document("/tmp/example.rfmap")
            recent_documents.clear_recent_documents()
        native.assert_not_called()

    def test_public_api_uses_the_application_native_history(self) -> None:
        paths = [Path("/data/newest.rfmap"), Path("/data/older.json")]
        with (
            mock.patch.object(recent_documents.sys, "platform", "darwin"),
            mock.patch.object(recent_documents, "_mac_recent_documents") as native,
            tempfile.TemporaryDirectory() as directory,
        ):
            native.return_value.list.return_value = paths
            self.assertEqual(recent_documents.list_recent_documents(), paths)
            path = Path(directory) / "nested" / ".." / "记录.rfmap"
            recent_documents.record_recent_document(path)
            native.return_value.record.assert_called_once_with(
                Path(directory).resolve() / "记录.rfmap"
            )
            recent_documents.clear_recent_documents()
            native.return_value.clear.assert_called_once_with()

    def test_native_list_preserves_order_and_unicode_and_skips_non_file_urls(self) -> None:
        native = object.__new__(recent_documents._MacRecentDocuments)
        native._objc = mock.Mock()
        paths = ["/data/记录 #1.rfmap", "/data/older.json"]

        def send(receiver, selector, result_type=ctypes.c_void_p,
                 argument_types=(), *arguments):
            if selector == b"recentDocumentURLs":
                return "urls"
            if selector == b"count":
                return 3
            if selector == b"objectAtIndex:":
                return arguments[0]
            if selector == b"isFileURL":
                return receiver != 1
            if selector == b"fileSystemRepresentation":
                return paths[receiver // 2].encode("utf-8")
            return "object"

        native._send = mock.Mock(side_effect=send)
        self.assertEqual(native.list(), [Path(path) for path in paths])
        self.assertEqual(native._send.call_args, mock.call("object", b"drain", None))

    def test_native_record_converts_file_path_without_url_escaping(self) -> None:
        native = object.__new__(recent_documents._MacRecentDocuments)
        native._objc = mock.Mock()
        native._objc.objc_getClass.side_effect = lambda name: name
        native._send = mock.Mock(return_value="object")
        native.record(Path("/data/记录 #1.rfmap"))
        native._send.assert_any_call(
            b"NSString", b"stringWithUTF8String:", ctypes.c_void_p,
            (ctypes.c_char_p,), "/data/记录 #1.rfmap".encode("utf-8"),
        )
        native._send.assert_any_call(
            b"NSURL", b"fileURLWithPath:", ctypes.c_void_p,
            (ctypes.c_void_p,), "object",
        )
        native._send.assert_any_call(
            "object", b"noteNewRecentDocumentURL:", None,
            (ctypes.c_void_p,), "object",
        )

    def test_native_clear_uses_document_controller(self) -> None:
        native = object.__new__(recent_documents._MacRecentDocuments)
        native._objc = mock.Mock()
        native._send = mock.Mock(return_value="object")
        native.clear()
        native._send.assert_any_call(
            "object", b"clearRecentDocuments:", None, (ctypes.c_void_p,), None,
        )


if __name__ == "__main__":
    unittest.main()
