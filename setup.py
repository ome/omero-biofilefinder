import os
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop
from setuptools.command.install import install
from setuptools.command.sdist import sdist

ROOT = os.path.dirname(os.path.abspath(__file__))
BFF_DIR = os.path.join(ROOT, "biofile-finder")
DIST_DIR = os.path.join(
    ROOT, "omero_biofilefinder", "static", "omero_biofilefinder", "dist"
)
BFF_VERSION_FILE = os.path.join(ROOT, "omero_biofilefinder", "version_bff.py")


def get_biofilefinder_commit_info():
    """
    Get the commit hash and date of the biofile-finder submodule.

    Returns a dict with 'hash' and 'date' keys, or None if unable to retrieve.
    """
    try:
        if not os.path.exists(BFF_DIR):
            return None

        # Get the current commit hash
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=BFF_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        # Get the commit date
        commit_date = subprocess.check_output(
            ["git", "log", "-1", "--format=%ci"],
            cwd=BFF_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

        return {
            "hash": commit_hash[:8],  # First 8 characters of the hash
            "date": commit_date,
        }
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        # git command failed or biofile-finder directory doesn't exist
        return None


def write_version_file(bff_commit_info):
    """
    Write BioFile Finder commit info to version_bff.py
    """
    info_repr = repr(bff_commit_info)
    new_content = f"BFF_COMMIT_INFO = {info_repr}\n"

    with open(BFF_VERSION_FILE, "w") as f:
        f.write(new_content)

    if bff_commit_info:
        print(f"BioFile Finder commit info: {bff_commit_info['hash']}")
    else:
        print("BioFile Finder commit info not available")


def require_npm(command, strict=False):
    """
    Decorator to run NPM prerequisites for building the BioFile Finder app.

    If the dist directory doesn't exist or strict=True, cd into biofile-finder,
    run npm install and npm run build, then cd back to the root.
    """

    class WrappedCommand(command):
        def run(self):
            # Check if we need to run npm build
            if strict or not os.path.exists(DIST_DIR):
                print("Building BioFile Finder app...")
                original_dir = os.getcwd()
                try:
                    os.chdir(BFF_DIR)
                    self.spawn(["npm", "install"])
                    # Set the PUBLIC_PATH environment variable for the build
                    env = os.environ.copy()
                    # all links to js, css etc must be relative, so that we
                    # can deploy the bff app under the biofilefinder/bff/app/ URL
                    env["PUBLIC_PATH"] = "auto"
                    subprocess.run(
                        ["npm", "--prefix", "packages/web", "run", "build"],
                        env=env,
                        check=True,
                    )
                    print("NPM build completed.")
                finally:
                    os.chdir(original_dir)

                # run deploy_build.sh to copy built assets to omero_biofilefinder
                self.spawn(["sh", "deploy_build.sh"])

            # Get and write biofile-finder commit info
            bff_commit_info = get_biofilefinder_commit_info()
            write_version_file(bff_commit_info)

            command.run(self)

    return WrappedCommand


# Apply the npm build wrapper to relevant commands
cmdclass = {
    "build_py": require_npm(build_py),
    "install": require_npm(install),
    "sdist": require_npm(sdist, strict=True),
    "develop": require_npm(develop),
}

# Also handle editable installs (pip install -e .)
try:
    from setuptools.command.editable_wheel import editable_wheel

    cmdclass["editable_wheel"] = require_npm(editable_wheel)
except ImportError:  # setuptools < 64
    pass

setup(cmdclass=cmdclass)
