from distutils.core import setup
from setuptools import find_packages
import sys
setup(
  name = 'pyOpenBCI',         # How you named your package folder (MyLib)
  packages = find_packages(),   # Chose the same as "name"
  version = '0.4',      # Start with a small number and increase it with every change you make
  license='MIT',        # Chose a license from here: https://help.github.com/articles/licensing-a-repository
  description = 'A lib for controlling OpenBCI devices',   # Give a short description about your library
  author = 'OpenBCI, Inc.',                   # Type in your name
  author_email = 'contact@openbci.com',      # Type in your E-Mail
  url = 'https://github.com/andreaortuno/pyOpenBCI',   # Provide either the link to your github or to your website
  download_url = 'https://github.com/andreaortuno/pyOpenBCI/archive/0.3.tar.gz',    # I explain this later on
  keywords = ['device', 'control', 'eeg', 'emg', 'ekg', 'ads1299', 'openbci', 'ganglion', 'cyton', 'wifi'],   # Keywords that define your package best
  install_requires=[            # I get to this in a second
          'numpy',
          'pyserial',
          'bitstring',
          'urllib2',
          'xmltodict',
          'requests',
      ]+ ["bluepy >= 2.0"] if sys.platform.startswith("linux") else [],
  classifiers=[
    'Development Status :: 3 - Alpha',      # Chose either "3 - Alpha", "4 - Beta" or "5 - Production/Stable" as the current state of your package
    'Intended Audience :: Developers',      # Define that your audience are developers
    'Topic :: Software Development :: Build Tools',
    'License :: OSI Approved :: MIT License',   # Again, pick a license
    'Programming Language :: Python :: 3',      #Specify which pyhton versions that you want to support
    'Programming Language :: Python :: 2.7',
    'Programming Language :: Python :: 3.4',
    'Programming Language :: Python :: 3.6',
  ],
)
