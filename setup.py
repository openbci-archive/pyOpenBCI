from distutils.core import setup
from setuptools import find_packages
import sys

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
  name = 'pyOpenBCI',
  packages = find_packages(),
  version = '0.13',
  license='MIT',
  description = 'A lib for controlling OpenBCI devices',
  long_description=long_description,
  long_description_content_type="text/markdown",
  author = 'OpenBCI, Inc.',
  author_email = 'contact@openbci.com',
  url = 'https://github.com/andreaortuno/pyOpenBCI',
  download_url = 'https://github.com/andreaortuno/pyOpenBCI/archive/0.13.tar.gz',
  keywords = ['device', 'control', 'eeg', 'emg', 'ekg', 'ads1299', 'openbci', 'ganglion', 'cyton', 'wifi'],
  install_requires=[
          'numpy',
          'pyserial',
          'bitstring',
          'xmltodict',
          'requests',
      ] + ["bluepy >= 1.2"] if sys.platform.startswith("linux") else [],
  classifiers=[
    'Development Status :: 3 - Alpha',
    'Intended Audience :: Developers',
    'Topic :: Software Development :: Build Tools',
    'License :: OSI Approved :: MIT License',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 2.7',
    'Programming Language :: Python :: 3.4',
    'Programming Language :: Python :: 3.6',
  ],
)
