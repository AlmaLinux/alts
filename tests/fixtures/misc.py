from pathlib import Path

import pytest


@pytest.fixture
def simple_bats_file(tmp_path: Path):
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
