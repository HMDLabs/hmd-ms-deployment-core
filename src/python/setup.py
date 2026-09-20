from setuptools import setup, find_packages
import pathlib

repo_dir = pathlib.Path(__file__).absolute().parent.parent.parent
version_file = repo_dir / "meta-data" / "VERSION"

with open(version_file, "r") as vfl:
    version = vfl.read().strip()

setup(
    name="hmd-ms-deployment-core",
    version=version,
    description="Registry and resolver core of the NeuronSphere deployment service",
    author="Alexander Burgoon",
    author_email="alex.burgoon@hmdlabs.io",
    license="BUSL-1.1",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[],
)
