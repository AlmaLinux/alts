import argparse
import json
import logging
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from plumbum import local

from alts.shared.models import OpennebulaConfig
from alts.shared.terraform import OpennebulaTfRenderer
from alts.worker import CONFIG, RESOURCES_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@contextmanager
def temporary_workdir():
    """
    Create and yield a temporary working directory to save rendered
    Terraform files to.

    This context manager creates a temporary directory, copies
    the tf.versions file into it, and yields the path. The directory
    is automatically cleaned up after usage.

    Yields:
        Path: Path to the temporary working directory.
    """
    workdir = Path(tempfile.mkdtemp())
    logger.info(f"Created working directory {workdir}")
    try:
        class_resources_dir = os.path.join(RESOURCES_DIR, 'opennebula')
        shutil.copy(
            os.path.join(
                class_resources_dir, OpennebulaTfRenderer.TF_VERSIONS_FILE
            ),
            os.path.join(workdir, OpennebulaTfRenderer.TF_VERSIONS_FILE),
        )
        yield workdir
    finally:
        try:
            shutil.rmtree(workdir)
            logger.info(f"Successfully removed {workdir}")
        except Exception as e:
            logger.error(f"Error while erasing working directory: {e}")


def load_platform_configs(path: Path) -> list[dict]:
    """
    Extract bs_platforms data from all JSON config files under the given path.
    """
    data_entries = []

    for config_file in path.glob("*.json"):
        with config_file.open("r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                logging.warning(
                    f"Skipping {config_file}: JSON decode error - {e}"
                )
                continue

        for distro in data:
            if not distro.get("opennebula_image_name"):
                continue
            for arch in distro.get("architectures", {}):
                data_entries.append({
                    "opennebula_image_name": distro["opennebula_image_name"],
                    "distr_version": distro["distr_version"],
                    "architecture": arch,
                    "test_flavor_name": distro.get("test_flavor_name"),
                    "test_flavor_version": distro.get("test_flavor_version"),
                })
    return data_entries


def deduplicate(data: list[dict]) -> list[dict]:
    """
    Remove duplicate dictionaries based on their JSON content.
    """
    seen = set()
    unique_data = []
    for entry in data:
        key = json.dumps(entry, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_data.append(entry)
    return unique_data


def extract_template_name(stdout) -> Optional[str]:
    """
    Extract the template_name value from Terraform plan stdout.

    Args:
        stdout (str): The standard output from a Terraform plan command.

    Returns:
        Optional[str]: The extracted template name, or None if not found.
    """
    # Looking for [template_name = "..."] in stdout of terraform plan
    match = re.search(
        r'\+\s*template_name\s*=\s*"([^"]+)"', stdout, re.MULTILINE
    )
    if match:
        return match.group(1)
    logger.warning("template_name output not found in terraform plan output.")
    return None


def extract_template_date(s: str) -> Optional[datetime.date]:
    """
    Parse the date from the template name (format: .bYYYYMMDD).

    Args:
        s (str): Template name string.

    Returns:
        Optional[datetime.date]: Extracted date, or None if parsing fails.
    """
    match = re.search(r'\.b(\d{8})', s)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            return None
    return None


def is_older_than_2_weeks(d: date) -> bool:
    """Check if the given date is older than two weeks from today."""
    return d < (date.today() - timedelta(weeks=2))


def init_terraform(workdir: Path) -> bool:
    """
    Initialize Terraform in the specified working directory.

    Args:
        workdir (Path): Path to the Terraform working directory.

    Returns:
        bool: True if initialization succeeds, False otherwise.
    """
    code, _, stderr = (
        local['terraform'].with_cwd(workdir).run(('init', '-no-color'))
    )
    if code == 0:
        logger.info("Terraform initialized successfully.")
        return True
    logger.error(f"Terraform init failed: {stderr}")
    return False


def run_terraform_plan(workdir: Path) -> Optional[str]:
    """
    Run Terraform plan in the working directory and extract the template name.

    Args:
        workdir (Path): Path to the Terraform working directory.

    Returns:
        Optional[str]: Extracted template name if successful, otherwise None.
    """
    code, stdout, stderr = (
        local['terraform']
        .with_cwd(workdir)
        .run(
            args=(
                'plan',
                '-no-color',
                '--var-file',
                OpennebulaTfRenderer.TF_VARIABLES_FILE,
            ),
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )
    )
    if code != 0:
        logger.error(f"Terraform plan failed: {stderr}")
        return None
    return extract_template_name(stdout)


def check_template_for_platform(
    renderer: OpennebulaTfRenderer, platform: dict
) -> list[str]:
    """
    Get Terraform template names for a specific platform across channels.

    Renders and runs Terraform plans for 'beta' and 'stable' channels.

    Args:
        renderer (OpennebulaTfRenderer): Renderer to use for template generation.
        platform (dict): Platform data dictionary.

    Returns:
        list[str]: List of found template names (may be empty).
    """
    templates = []
    # build-systems-configs might only have the major distr version
    # so we need to accept any minor versions in vm templates
    dist_version = platform['distr_version']
    if not '.' in dist_version:
        dist_version = f"{platform['distr_version']}\.\d+"
    for channel in CONFIG.allowed_channel_names:
        renderer.render_tf_main_file(
            dist_name=platform["opennebula_image_name"],
            dist_version=dist_version,
            dist_arch=platform["architecture"],
            vm_disk_size=0,
            vm_ram_size=0,
            vm_name='vm',
            package_channel=channel,
            test_flavor_name=platform.get("test_flavor_name"),
            test_flavor_version=platform.get("test_flavor_version"),
        )
        template = run_terraform_plan(renderer.work_dir)
        if template:
            templates.append(template)
    return templates


def test_for_outdated_templates(workdir: str, bs_configs_path: Path):
    """
    Run checks to detect missing or outdated OpenNebula templates.

    Loads platform config data, checks for available templates via Terraform,
    and verifies that template dates are within the last two weeks.

    Args:
        workdir (str): Temporary working directory for Terraform.
        bs_configs_path (Path): Path to the root of the build-system-configs repo.

    Raises:
        RuntimeError: If any templates are missing or outdated.
    """

    bs_configs_path = bs_configs_path / 'build_platforms'
    bs_data = deduplicate(load_platform_configs(bs_configs_path))

    renderer = OpennebulaTfRenderer(workdir)
    renderer.render_tf_variables_file()

    if not init_terraform(workdir):
        raise RuntimeError("Terraform initialization failed.")

    found_templates = []
    outdated_templates = []
    not_found_platforms = []

    for platform in bs_data:
        logger.info(
            f"Checking images for {platform['opennebula_image_name']}-{platform['distr_version']}-{platform['architecture']}"
        )
        templates = check_template_for_platform(renderer, platform)
        if not templates:
            not_found_platforms.append(platform)
            continue
        found_templates.extend(templates)

    for template in found_templates:
        template_date = extract_template_date(template)
        if template_date and is_older_than_2_weeks(template_date):
            outdated_templates.append(template)

    for i, pl in enumerate(not_found_platforms, start=1):
        logger.warning(f"{i}. Platform template not found: {pl}")

    for i, template in enumerate(outdated_templates, start=1):
        logger.warning(f"{i}. Outdated template found: {template}")

    if outdated_templates or not_found_platforms:
        raise RuntimeError("Some templates are missing or outdated.")


def init_celery_config(args):
    if not CONFIG.opennebula_config:
        CONFIG.opennebula_config = OpennebulaConfig(
            rpc_endpoint=args.rpc_endpoint,
            username=args.opennebula_user,
            password=args.opennebula_password,
            vm_group=args.vm_group,
            network=args.opennebula_network,
        )


def init_parser():
    parser = argparse.ArgumentParser(
        description="Check for outdated OpenNebula templates."
    )
    parser.add_argument(
        "--bs-configs-path",
        required=True,
        type=Path,
        help="Path to the build-system-configs directory",
    )
    parser.add_argument(
        "--rpc-endpoint",
        required=True,
        type=str,
        help="Opennebula rpc endpoint",
    )
    parser.add_argument(
        "--opennebula-user", required=True, type=str, help="Opennebula username"
    )
    parser.add_argument(
        "--opennebula-password",
        required=True,
        type=str,
        help="Opennebula password",
    )
    parser.add_argument(
        "--opennebula-network",
        type=str,
        default="build-system-developers",
        help="Opennebula network",
    )
    parser.add_argument(
        "--vm-group", required=False, type=str, help="Opennebula vm group"
    )
    return parser


def main():
    parser = init_parser()
    args = parser.parse_args()
    init_celery_config(args)

    with temporary_workdir() as workdir:
        test_for_outdated_templates(workdir, args.bs_configs_path)


if __name__ == '__main__':
    main()
