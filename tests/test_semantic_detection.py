import unittest

from solidlsp.ls_config import LanguageServerId

from tracker.repository import SourceFile
from tracker.semantic import _LANGUAGE_EXTENSIONS, _detect_language_servers


def source(path: str) -> SourceFile:
    return SourceFile(path=path, content="")


class SemanticLanguageDetectionTests(unittest.TestCase):
    def test_detects_java_go_and_python_together(self) -> None:
        detected = _detect_language_servers(
            (
                source("backend/src/Main.java"),
                source("services/orders/main.go"),
                source("workers/avatar.py"),
            ),
            LanguageServerId,
        )

        self.assertEqual({item.value for item in detected}, {"java", "go", "python"})

    def test_prefers_code_languages_before_config_languages_at_cap(self) -> None:
        detected = _detect_language_servers(
            (
                *(source(f"config/{index}.yaml") for index in range(20)),
                source("src/Main.java"),
                source("cmd/server/main.go"),
                source("worker/main.py"),
            ),
            LanguageServerId,
            max_languages=3,
        )

        self.assertEqual({item.value for item in detected}, {"java", "go", "python"})

    def test_uses_framework_server_instead_of_duplicate_typescript(self) -> None:
        svelte = _detect_language_servers(
            (source("src/App.svelte"), source("src/client.ts")),
            LanguageServerId,
        )
        angular = _detect_language_servers(
            (source("angular.json"), source("src/app.component.ts")),
            LanguageServerId,
        )

        self.assertEqual([item.value for item in svelte], ["svelte"])
        self.assertEqual([item.value for item in angular], ["angular"])

    def test_every_detectable_language_exists_in_installed_serena(self) -> None:
        available = {item.value for item in LanguageServerId}
        self.assertEqual(set(_LANGUAGE_EXTENSIONS) - available, set())


if __name__ == "__main__":
    unittest.main()
