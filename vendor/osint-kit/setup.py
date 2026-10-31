from setuptools import setup, find_packages
setup(name="osint-kit", version="1.0.0", packages=find_packages(),
      entry_points={"console_scripts": ["osint=osint_kit.cli:main"]})
