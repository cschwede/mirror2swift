#!/usr/bin/env python

from distutils.core import setup

setup(
    name='mirror2swift',
    version='0.1',
    description='Tool to mirror a HTTP site to OpenStack Swift',
    author='Christian Schwede',
    author_email='info@cschwede.de',
    url='http://www.github.com/cschwede/mirror2swift',
    packages=['mirror2swift'],
    scripts=['bin/mirror2swift'],
)
