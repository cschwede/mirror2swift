mirror2swift
============

A small utility to clone a public HTTP mirror to a Swift container, for example
to keep a local clone of a RPM repository in your CI environment.

Quick Install
-------------

1) Install:

    git clone git://github.com/cschwede/mirror2swift.git
    cd mirror2swift
    sudo python setup.py install

2) Create a config file like this. You need a temp url key for Swift, and the
   Swift container has to be public readable (including listings):

    first:
      mirrors:
      - name: base
        url: 'http://some.mirror.eu/linux/distributions/centos/7/os/x86_64/'
        prefix: 'base/'
      swift:
        url: 'http://127.0.0.1:8080/v1/AUTH_tester/repomirror/'
        key: 'secret'

3) Run the tool:

    mirror2swift config.yaml

Done!
