from pathlib import Path

import pytest


@pytest.fixture
def simple_bats_file(tmp_path: Path) -> str:
    bats_file = tmp_path / 'simple.bats'
    bats_file.write_text(
        """
        #!/usr/bin/env bats

        @test "addition using bc" {
          result="$(echo 2+2 | bc)"
          [ "$result" -eq 4 ]
        }
        """
    )
    return str(bats_file)


@pytest.fixture
def simple_shell_script(tmp_path: Path) -> str:
    bats_file = tmp_path / 'simple'
    bats_file.write_text(
        """
        #!/usr/bin/bash
        echo "ϴ Ϣ Ñ Ђ Љ"
        """
    )
    return str(bats_file)
