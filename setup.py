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
