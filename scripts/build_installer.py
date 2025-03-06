#!/usr/bin/env python
"""Script to create platform-specific Deadline Client installers using InstallBuilder."""

import argparse
import os
import sys
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import List, NamedTuple
from pathlib import Path

# This is derived from <installerFilename> in installer/DeadlineCloudClient.xml
# See "Supported Platforms" table in https://releases.installbuilder.com/installbuilder/docs/installbuilder-userguide.html
INSTALLER_FILENAMES = {
    "windows-x64": "DeadlineCloudClient-windows-x64-installer.exe",
    "linux-x64": "DeadlineCloudClient-linux-x64-installer.run",
    "osx": "DeadlineCloudClient-osx-installer.app",
}
INSTALL_BUILDER_VERSION = "23.11.0"
EVALUATION_VERSION_STRING = "Built with an evaluation version of InstallBuilder"


class BadRCError(Exception):
    pass


class EvaluationBuildError(Exception):
    pass


class UnsupportedOSError(Exception):
    pass


class RequiredArg(NamedTuple):
    """
    Structure to represent a required CLI argument. Only used to provide better error messaging
    """

    argument: str
    attr: str


def run(cmd, cwd=None, env=None, echo=True):
    if echo:
        sys.stdout.write(f"Running cmd: {cmd}\n")
    kwargs = {
        "shell": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if isinstance(cmd, list):
        kwargs["shell"] = False
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    p = subprocess.Popen(cmd, **kwargs)
    stdout, stderr = p.communicate()
    output = stdout.decode("utf-8") + stderr.decode("utf-8")
    if p.returncode != 0:
        raise BadRCError(f"Bad rc ({p.returncode}) for cmd '{cmd}': {output}")
    return output


def download_from_s3(bucket_name, output_folder):
    key = "install_builder/{archive_name}"
    if sys.platform.startswith("darwin"):
        key = key.format(archive_name="TODOTODOTODO")
    elif sys.platform.startswith("win32"):
        key = key.format(archive_name="TODOTODOTODO")
    elif sys.platform.startswith("linux"):
        key = key.format(archive_name="VMware-InstallBuilder-Professional-linux.tar.gz")
    else:
        raise UnsupportedOSError(f"Unsupported OS for install builder: {sys.platform}")
    dest_path = os.path.join(output_folder, os.path.basename(key))
    print(f"Downloading {key} from s3:\\\\{bucket_name}")
    import boto3

    s3 = boto3.client("s3")
    s3.download_file(bucket_name, key, dest_path)
    return dest_path


def get_default_installbuilder_location() -> str:
    """
    Returns the default location where InstallBuilder Professional will be installed depending on the platform.
    """

    install_builder_path = ""

    if sys.platform.startswith("darwin"):
        install_builder_path = (
            f"/Applications/InstallBuilder Professional {INSTALL_BUILDER_VERSION}/"
        )
    elif sys.platform.startswith("win32"):
        install_builder_path = (
            f"C:\\Program Files\\InstallBuilder Professional {INSTALL_BUILDER_VERSION}\\"
        )
    elif sys.platform.startswith("linux"):
        install_builder_path = f"/opt/installbuilder-{INSTALL_BUILDER_VERSION}/"

    return install_builder_path


def build_installer(
    workdir: str,
    component_file_path: str,
    install_builder_location: str,
    license_file_path: str,
    s3bucket: str,
    platform: str,
    local_dev_build: bool,
):
    if not local_dev_build:
        install_builder_archive = download_from_s3(s3bucket, workdir)
        shutil.unpack_archive(install_builder_archive, workdir)
        install_builder_location = workdir
    else:
        if not install_builder_location:
            raise FileNotFoundError(
                "Could not find a default InstallBuilder path. Please specify one with '--install-builder-location'."
            )

        if not Path(install_builder_location).is_dir():
            raise FileNotFoundError(
                f"InstallBuilder path '{install_builder_location}' must be a directory containing 'bin/builder'."
            )

    if license_file_path:
        shutil.copy(license_file_path, os.path.join(workdir, "license.xml"))

    install_builder_cli = os.path.join(install_builder_location, "bin", "builder")
    out_dir = os.path.join(workdir, "out")
    installer_version = os.getenv("INSTALLER_VERSION") if not local_dev_build else "00000000"
    output = run(
        [
            install_builder_cli,
            "build",
            component_file_path,
            platform,
            "--setvars",
            f"project.outputDirectory={out_dir}",
            f"project.version={installer_version[:8]}-{datetime.today().date()}",
        ]
    )
    sys.stdout.write(
        f"{'-'*30}\nBegin Install Builder Output\n{'-'*30}\n"
        f"{output}\n"
        f"{'-'*30}\nEnd Install Builder Output\n{'-'*30}\n"
    )

    if EVALUATION_VERSION_STRING in output and not local_dev_build:
        raise EvaluationBuildError("InstallBuilder was detected using an evaluation version.")
    elif local_dev_build and EVALUATION_VERSION_STRING not in output:
        raise EvaluationBuildError(
            "InstallBuilder was not detected using an evaluation version when running a dev build. "
            "This could indicate that the error messaging when using an evaluation version has changed.\n"
            "Please check the InstallBuilder logs to confirm if the error messaging has changed from "
            f"'{EVALUATION_VERSION_STRING}' and update the build_installer.py script accordingly."
        )
    return out_dir


def create_dc_pyinstaller_artifact(archive_path: str):
    """
    Creates pyinstaller deadline-cloud artifact
    """
    os.environ["PYINSTALLER_OUT_FILE"] = archive_path
    run("hatch run installer:build")
    run("hatch run installer:make_exe")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    prod_required_args: List[RequiredArg] = []

    parser.add_argument("--local-dev-build", action=argparse.BooleanOptionalAction, help=(""))
    parser.add_argument(
        "--install-builder-s3-bucket",
        help="The name of S3 Bucket that contains Install Builder.",
    )  # Required for non-local-builds
    prod_required_args.append(
        RequiredArg("--install-builder-s3-bucket", "install_builder_s3_bucket")
    )
    parser.add_argument(
        "--install-builder-location",
        help="The InstallBuilder location, containing 'bin/builder'. Leave this blank to look for a Professional edition installation in the default location.",
        default=get_default_installbuilder_location(),
    )
    parser.add_argument(
        "--install-builder-license-path",
        help="The path to the file containing the InstallBuilder license. This can be set to NO_LICENSE to skip downloading the license.",
    )  # Required for non-local builds
    prod_required_args.append(
        RequiredArg("--install-builder-license-path", "install_builder_license_file")
    )
    parser.add_argument(
        "--install-builder-license-secret-id",
        help="The ID (ARN or name) of Secret that contains the InstallBuilder license. This can be set to NO_LICENSE to skip downloading the license.",
    )  # Required for non-local-builds
    prod_required_args.append(
        RequiredArg("--install-builder-license-secret-id", "install_builder_license_secret_id")
    )
    parser.add_argument(
        "--no-cleanup",
        dest="cleanup",
        action="store_false",
        help=(
            "Leave the build folder produced by pyinstaller. This can be " "useful for debugging."
        ),
    )
    parser.add_argument(
        "--platform",
        required=True,
        help="The platform to build an installer for",
        choices=("windows-x64", "linux-x64", "osx"),
    )
    parser.add_argument(
        "--output-dir",
        required=False,
        default=None,
        help="The directory to create the installer in. Default is the current directory.",
    )
    parser.add_argument(
        "--dc-artifact-path", help=("Path to the Deadline Client pyinstaller ZIP archive")
    )  # Required for non-local-builds
    prod_required_args.append(RequiredArg("--dc-artifact-path", "dc_artifact_path"))

    args = parser.parse_args()
    if not args.local_dev_build:
        missing_args = []
        for required_arg in prod_required_args:
            if getattr(args, required_arg.attr) is None:
                missing_args.append(required_arg.argument)
        if missing_args:
            parser.error(
                "The following arguments are required for non-dev builds: "
                f"{', '.join(missing_args)}\n"
            )
    else:
        if os.environ.get("CODEBUILD_BUILD_ID") is not None:
            parser.error("--local-dev-build cannot be used when running in CodeBuild.")

    with tempfile.TemporaryDirectory() as workdir:
        run("python -m pip install --upgrade pip")
        print(f"cwd: {os.getcwd()}")
        print(f"working directory: {workdir}")

        installer_folder = Path(os.path.abspath(__file__)).parent.parent / "installer"
        component_file_path = os.path.join(installer_folder, "DeadlineCloudClient.xml")

        # Create and unpack pyinstaller archive into /install/components/DeadlineClient
        components_dir = os.path.join(installer_folder, "components")
        os.makedirs(os.path.join(components_dir), exist_ok=True)
        archive_path = (
            args.dc_artifact_path
            if args.dc_artifact_path
            else os.path.join(workdir, "deadline.zip")
        )

        if args.local_dev_build and not args.dc_artifact_path:
            create_dc_pyinstaller_artifact(workdir, archive_path)

        shutil.unpack_archive(archive_path, os.path.join(components_dir, "DeadlineClient"), "zip")

        try:
            installer_dir = build_installer(
                workdir=workdir,
                component_file_path=component_file_path,
                install_builder_location=args.install_builder_location,
                license_file_path=args.install_builder_license_path
                if args.install_builder_license_path != "NO_LICENSE"
                else None,
                platform=args.platform,
                local_dev_build=args.local_dev_build,
                s3bucket=args.install_builder_s3_bucket,
            )
        except Exception as e:
            # always want to delete the components_dir if build fails
            shutil.rmtree(components_dir)
            raise e

        installer_filename = INSTALLER_FILENAMES[args.platform]
        installer_path = os.path.join(installer_dir, installer_filename)

        # The macOS .app installer will always be a directory, not a file.
        # Other OS installers will be files.
        if (
            not os.path.isdir(installer_path)
            if args.platform == "osx"
            else not os.path.isfile(installer_path)
        ):
            raise FileNotFoundError(
                f"Expected installer file {installer_filename} not found in {installer_dir}.\n"
                f"Found:\n\t{os.linesep.join(os.listdir(installer_dir))}"
            )

        output_path = installer_filename
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            output_path = os.path.join(args.output_dir, output_path)

        shutil.move(installer_path, output_path)

        if args.cleanup:
            shutil.rmtree(components_dir)
            print(f"Deleted build directory: {components_dir}")


if __name__ == "__main__":
    main()
