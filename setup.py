import glob
import os
import shutil
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = os.path.dirname(os.path.abspath(__file__))
BFF_DIR = os.path.join(ROOT, "biofile-finder")
DIST_DIR = os.path.join(
    ROOT, "omero_biofilefinder", "static", "omero_biofilefinder", "dist"
)
print("-----> ROOT", ROOT)
print("-----> BFF_DIR", BFF_DIR)
print("*************** Setting up BioFile Finder build... DIST_DIR", DIST_DIR)


def build_bff(announce=print):
    print(
        "** * * * * * * * ****** Building BioFile Finder app... build_bff(announce=",
        announce,
        ")",
    )
    os.makedirs(BFF_DIR, exist_ok=True)
    print("currenct working directory:", os.getcwd())
    os.chdir(BFF_DIR)
    # list the contents of the current directory
    print(" -----> Contents of BFF_DIR:", os.listdir(BFF_DIR))

    if not os.path.exists(os.path.join(BFF_DIR, "package.json")):
        # e.g. building a wheel from an sdist: use the bundled assets
        announce("biofile-finder submodule not found: using existing dist assets")
        return
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError(
            "npm is required to build the BioFile Finder app but was not "
            "found on PATH. Install Node.js/npm and try again."
        )
    subprocess.check_call([npm, "install"], cwd=BFF_DIR)
    # Set the PUBLIC_PATH environment variable for the build
    os.environ["PUBLIC_PATH"] = "auto"
    subprocess.check_call(
        [npm, "--prefix", "packages/web", "run", "build"], cwd=BFF_DIR
    )
    print(" - - - - - -  - > Building BioFile Finder app...", DIST_DIR)
    os.makedirs(DIST_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(DIST_DIR, "*")):
        print("------> Removing old assets from", old)
        if os.path.isdir(old):
            shutil.rmtree(old)
        else:
            os.remove(old)
    for built in glob.glob(os.path.join(BFF_DIR, "packages", "web", "dist", "*")):
        print("------>Copying built asset from", built)
        if os.path.isdir(built):
            shutil.copytree(built, os.path.join(DIST_DIR, os.path.basename(built)))
        else:
            shutil.copy2(built, DIST_DIR)


class BuildPyWithBff(build_py):
    """Build the BioFile Finder app and copy its assets before packaging."""

    def run(self):
        build_bff(self.announce)
        super().run()


cmdclass = {"build_py": BuildPyWithBff}

try:
    # pip install -e . does not call build_py.run(), so hook it as well
    from setuptools.command.editable_wheel import editable_wheel

    class EditableWheelWithBff(editable_wheel):
        def run(self):
            build_bff(self.announce)
            super().run()

    cmdclass["editable_wheel"] = EditableWheelWithBff
except ImportError:  # setuptools < 64
    pass

setup(cmdclass=cmdclass)
