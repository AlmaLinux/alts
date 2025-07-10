import argparse
import json
import logging
import os
import re
import shutil
import tempfile

from collections import namedtuple, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, List, Set

from plumbum import local

from alts.shared.constants import SUPPORTED_ARCHITECTURES
from alts.shared.models import OpennebulaConfig
from alts.shared.terraform import OpennebulaTfRenderer
from alts.worker import CONFIG, RESOURCES_DIR

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

PlatformInfo = namedtuple(
    "PlatformInfo",
    [
        "platform_name",
        "version",
        "arch",
        "flavor_name",
        "flavor_version",
    ],
)


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


def load_platform_configs(path: Path) -> Set[PlatformInfo]:
    """
    Extract bs_platforms data from all JSON config files under the given path.
    Parses it into a set of PlaformInfo structure.
    """
    data_entries = set()

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
                platform_info = PlatformInfo(
                    platform_name=distro["opennebula_image_name"],
                    version=distro["distr_version"],
                    arch=arch,
                    flavor_name=distro.get("test_flavor_name", "base_image"),
                    flavor_version=distro.get("test_flavor_version", None),
                )
                data_entries.add(platform_info)
    return data_entries


def extract_template_names(stdout) -> Optional[str]:
    """
    Extract the template_name value from Terraform plan stdout.

    Args:
        stdout (str): The standard output from a Terraform plan command.

    Returns:
        Optional[str]: The extracted template name, or None if not found.
    """
    # Looking for template_names = [list_of_templates] in stdout of terraform plan
    match = re.search(
        r'\+ template_names\s*=\s*\[\n(.*?)\n\s*\]', stdout, re.DOTALL
    )
    if match:
        block = match.group(1)
        return re.findall(r'\+\s*"([^"]+)"', block)
    logger.warning("template_names output not found in terraform plan output.")
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
        .with_env(
            TF_LOG='TRACE', TF_LOG_PROVIDER='TRACE', TF_LOG_PATH='terraform.log'
        )
        .with_cwd(workdir)
        .run(
            args=(
                'plan',
                '-no-color',
                '-var=load_all_templates=true',
                '--var-file',
                OpennebulaTfRenderer.TF_VARIABLES_FILE,
            ),
            retcode=None,
            timeout=CONFIG.provision_timeout,
        )
    )
    if code != 0:
        logger.error(f"Terraform plan failed: {stderr}")
        raise RuntimeError("Terraform plan failed.")
    return extract_template_names(stdout)


def check_template_for_platform(
    renderer: OpennebulaTfRenderer, platforms: Set[PlatformInfo], workdir
) -> Set[str]:
    """
    Get Terraform template names for a all platforms.

    Builds terraform regex that matches all platforms data regex
    Renders and runs Terraform plans for 'beta' and 'stable' channels.

    Args:
        renderer (OpennebulaTfRenderer): Renderer to use for template generation.
        platform (dict): Platform data dictionary.

    Returns:
        list[str]: List of found template names (may be empty).
    """
    all_templates = set()

    # Create regex strings to look for all suitable VM templates
    all_dist_versions = r'\d+(?:\.\d+)?'
    all_image_names = '|'.join(set([pl.platform_name for pl in platforms]))
    all_architectures = '|'.join(SUPPORTED_ARCHITECTURES)
    all_test_flavor_names = '|'.join(
        set(pl.flavor_name for pl in platforms if pl.flavor_name)
    )
    all_test_flavor_names += '|base_image'
    optional_test_flavor_version = '|'.join(
        set(pl.flavor_version for pl in platforms if pl.flavor_version)
    )

    for channel in CONFIG.allowed_channel_names:
        renderer.render_tf_main_file(
            dist_name=f"({all_image_names})",
            dist_version=all_dist_versions,
            dist_arch=f"({all_architectures})",
            vm_disk_size=0,
            vm_ram_size=0,
            vm_name='vm',
            package_channel=channel,
            test_flavor_name=f'({all_test_flavor_names})',
            test_flavor_version=f'({optional_test_flavor_version})?',
        )
        templates = run_terraform_plan(workdir)
        if templates:
            all_templates.update(templates)
    return all_templates


def get_found_platforms(found_templates: List[str]) -> Set[PlatformInfo]:
    """
    Parses template names into PlatformInfo structure and deduplicates

    Arguments:
        List[str] - list of templates' names returned by terraform

    Returns:
        Set[PlatformInfo]
    """
    pattern = re.compile(
        r'^'
        r'(?P<platform_name>\w+(?:-\w+)*)'
        r'-(?P<version>\d+(?:\.\d+)?)'
        r'-(?P<arch>[\w]+)'
        r'\.(?P<flavor_name>\w+)'
        r'(?:-(?P<flavor_version>.+))?'
        r'\.test_system\b'
    )

    result = set()
    for tmpl in found_templates:
        match = pattern.search(tmpl)
        if match:
            result.add(
                PlatformInfo(
                    platform_name=match.group("platform_name"),
                    version=match.group("version"),
                    arch=match.group("arch"),
                    flavor_name=match.group("flavor_name"),
                    flavor_version=match.group("flavor_version"),
                )
            )
    return result


def check_minor_versions(
    not_found_platforms: Set[PlatformInfo],
    found_platforms: Set[PlatformInfo],
):
    """
    Gets plaforms with only major version specified
    in the list of not found plaforms
    When corresponding templates with minor versions are found,
    excludes the plaform from not_found_plaforms list
    """
    platforms_major_versions = {
        pl for pl in not_found_platforms if '.' not in pl.version
    }
    found_index = defaultdict(list)
    for pl in found_platforms:
        key = (pl.platform_name, pl.arch, pl.flavor_name, pl.flavor_version)
        found_index[key].append(pl)

    for major in platforms_major_versions:
        result = set()
        key = (
            major.platform_name,
            major.arch,
            major.flavor_name,
            major.flavor_version,
        )
        candidates = found_index.get(key, [])
        for found in candidates:
            if found.version == major.version or found.version.startswith(
                major.version + "."
            ):
                result.add(found.version)
        if result:
            logging.warning(
                f"Platform template not found: {major}\nCorresponding minor versions: {result}"
            )
            not_found_platforms.discard(major)


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
    bs_data = load_platform_configs(bs_configs_path)

    renderer = OpennebulaTfRenderer(workdir)
    renderer.render_tf_variables_file()

    if not init_terraform(workdir):
        raise RuntimeError("Terraform initialization failed.")

    found_templates = []
    outdated_templates = []
    not_found_platforms = []

    found_templates = check_template_for_platform(renderer, bs_data, workdir)
    found_platforms = get_found_platforms(found_templates)
    not_found_platforms = bs_data - found_platforms
    check_minor_versions(not_found_platforms, found_platforms)

    for template in found_templates:
        template_date = extract_template_date(template)
        if template_date and is_older_than_2_weeks(template_date):
            outdated_templates.append(template)

    for i, pl in enumerate(not_found_platforms, start=1):
        logger.warning(f"{i}. Platform template not found: {pl}")

    for i, template in enumerate(outdated_templates, start=1):
        logger.warning(f"{i}. Outdated template found: {template}")

    if outdated_templates or not_found_platforms:
        error_message = (
            f"{len(not_found_platforms)} templates are missing; "
            f"{len(outdated_templates)} are outdated."
        )
        raise RuntimeError(error_message)


def init_celery_config(args):
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
