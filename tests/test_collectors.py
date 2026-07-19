from __future__ import annotations

import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchman import collectors


def write_registry(
    root: Path,
    *,
    name: str,
    output: str,
    log_path: str | None = None,
) -> Path:
    declaration: dict[str, object] = {
        "name": name,
        "command": ["python3", f"examples/{name}.py"],
        "output": output,
        "expectations": {
            "min_size_bytes": 1,
            "expected_frequency_seconds": 60,
        },
    }
    if log_path is not None:
        declaration["log_path"] = log_path
    registry = root / "watchman" / "registry.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"jobs": [declaration]}),
        encoding="utf-8",
    )
    return registry


def inspection(job: str, path: Path, size: int, rows: int) -> dict[str, object]:
    source = {"path": str(path)}
    return {
        "job": job,
        "inspected_at": "2026-07-19T00:00:00+00:00",
        "output": {"status": "available", "path": str(path)},
        "observed": {
            "size_bytes": {
                "status": "available",
                "value": size,
                "source": source,
            },
            "row_count": {
                "status": "available",
                "value": rows,
                "source": source,
            },
        },
    }


class GenericCollectorTests(unittest.TestCase):
    def test_collects_text_artifact_log_history_and_registry_for_any_job(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = "synthetic_third_job"
            artifact = root / "artifacts" / "third.jsonl"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                "\n".join(json.dumps({"row": index}) for index in range(8)) + "\n",
                encoding="utf-8",
            )
            log = root / "logs" / "third.log"
            log.parent.mkdir()
            log.write_text(
                "\n".join(f"log {index}" for index in range(60)) + "\n",
                encoding="utf-8",
            )
            registry = write_registry(
                root,
                name=job,
                output="artifacts/third.jsonl",
                log_path="logs/third.log",
            )
            history = root / "watchman" / "history.jsonl"
            history.write_text(
                "\n".join(
                    json.dumps(inspection(job, artifact, index * 10, index))
                    for index in range(1, 12)
                )
                + "\n",
                encoding="utf-8",
            )

            evidence = collectors.collect_for_job(
                job,
                root=root,
                registry_path=registry,
                history_path=history,
            )

        items = evidence["items"]
        self.assertEqual(evidence["job"], job)
        self.assertEqual(len(items["log_tail"]["value"]), 50)
        self.assertEqual(len(items["artifact_first_3_lines"]["value"]), 3)
        self.assertEqual(len(items["artifact_last_5_lines"]["value"]), 5)
        self.assertEqual(
            len(items["size_row_and_output_count_trend"]["value"]), 10
        )
        self.assertEqual(
            items["registry_declaration"]["value"]["name"], job
        )
        for item in items.values():
            self.assertIn("source_id", item["source"])

    def test_missing_optional_log_is_explicitly_not_declared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_registry(
                root,
                name="plain_file_job",
                output="artifacts/output.txt",
            )
            history = root / "watchman" / "history.jsonl"
            history.touch()

            evidence = collectors.collect_for_job(
                "plain_file_job",
                root=root,
                history_path=history,
            )

        self.assertEqual(
            evidence["items"]["log_tail"]["reason"],
            "not_declared_for_job",
        )
        self.assertEqual(
            evidence["items"]["artifact_metadata"]["reason"],
            "output_not_found",
        )

    def test_tar_members_are_collected_by_file_type_not_job_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.db"
            source.write_bytes(b"database")
            archive = root / "artifacts" / "custom.tar.gz"
            archive.parent.mkdir()
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(source, arcname="source.db")
            os.utime(archive, (2, 2))
            write_registry(
                root,
                name="arbitrary_archive_job",
                output="artifacts/*.tar.gz",
            )
            history = root / "watchman" / "history.jsonl"
            history.touch()

            evidence = collectors.collect_for_job(
                "arbitrary_archive_job",
                root=root,
                history_path=history,
            )

        self.assertEqual(
            evidence["items"]["archive_members"]["value"], ["source.db"]
        )
        self.assertEqual(
            evidence["items"]["artifact_first_3_lines"]["reason"],
            "artifact_not_text_sampled",
        )


if __name__ == "__main__":
    unittest.main()
