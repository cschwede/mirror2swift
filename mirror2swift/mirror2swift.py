#!/usr/bin/env python
import argparse
import hashlib
import hmac
import lxml.html
import random
import re
import requests
import time
import urllib
import urlparse
import yaml
import gzip
import StringIO
import logging as log
import os
import sys
import subprocess
try:
    import yum
except ImportError:
    pass


def get_weblisting_uri_list(base_url, suffix=""):
    resp = requests.get(base_url + suffix)
    dom = lxml.html.fromstring(resp.content)
    uri_list = []
    for uri in dom.xpath('//a/@href'):
        if uri.startswith('../') or uri.startswith('?'):
            continue
        uri = suffix + uri
        if uri.endswith('/'):
            uri_list += get_weblisting_uri_list(base_url, uri)
        else:
            uri_list.append(urllib.unquote_plus(uri))
    return uri_list


def get_repodata_uri_list(base_url):
    uri_list = ["repodata/repomd.xml"]
    repomd_url = "%s%s" % (base_url, uri_list[0])

    log.debug("Discovering %s" % repomd_url)
    resp = requests.get(repomd_url)
    dom = lxml.html.fromstring(resp.content)
    filelist = None
    for uri in dom.xpath('//location/@href'):
        uri_list.append(uri)

    filelist = filter(lambda x: x.endswith("primary.xml.gz"), uri_list)
    if len(filelist) != 1:
        raise RuntimeError("Couldn't find filelist in %s (%s)" % (
                           repomd_url, uri_list))
    log.debug("Getting package list: %s%s" % (base_url, filelist[0]))
    resp = requests.get("%s%s" % (base_url, filelist[0]))

    try:
        filelist = gzip.GzipFile(fileobj=StringIO.StringIO(resp.content)).read()
    except:
        filelist = resp.content

    # Extract packages list
    log.debug("Adding all primary packages location")
    dom = lxml.html.fromstring(filelist)
    for uri in dom.xpath('//location/@href'):
        uri_list.append(uri)
    return uri_list


def get_local_files_list(path):
    files_list = []
    if not os.path.isdir(path):
        log.error("%s: not a directory" % path)
        exit(1)
    for dirpath, dirnames, files in os.walk(path):
        if not files:
            continue
        local_dir_path = dirpath[len(path):]
        if not local_dir_path:
            files_list += files
        else:
            files_list += map(lambda x: "%s/%s" % (local_dir_path, x), files)
    if not files_list:
        log.error("%s: empty directory" % path)
    return files_list


def get_container_list(url, prefix=None):
    url += "?format=json"
    if prefix:
        url += "&prefix=%s" % prefix
    log.debug("Listing swift container %s" % url)
    resp = requests.get(url)
    return [o.get('name') for o in resp.json()]


def get_missing(uri_list, container_list):
    return set(uri_list) - set(container_list)


def get_unneeded(uri_list, container_list):
    return set(container_list) - set(uri_list)


def get_tempurl(path, key):
    expires = int(time.time() + 300)
    hmac_body = 'PUT\n%s\n%s' % (expires, path)
    sig = hmac.new(key, hmac_body, hashlib.sha1).hexdigest()
    return (sig, expires)


def force_update(url):
    # Return True when url shall be updated regardless of its Content-Length
    force = False
    if url.endswith('/repodata/repomd.xml'):
        force = True
    for gitfiles in ('info/refs', 'objects/info/packs', 'packed-refs',
                     'HEAD', 'FETCH_HEAD'):
        if url.endswith('/%s' % gitfiles):
            force = True
    return force


def local_path(url):
    if url[:4] != 'http':
        if os.path.exists(url):
            return True
        log.error("%s: local path doesn't exists" % url)
    return False


def upload_missing(download_url, swift_url, swift_key,
                   swift_ttl=False, update=False):
    if update and not force_update(download_url):
        if local_path(download_url):
            class LocalResp:
                def __init__(self, path):
                    size = os.stat(download_url).st_size
                    self.headers = {
                        'Content-Length': str(size)
                    }
            mirror_resp = LocalResp(download_url)
        else:
            mirror_resp = requests.head(download_url)
        swift_resp = requests.head(swift_url)
        if (mirror_resp.headers.get('Content-Length') ==
                swift_resp.headers.get('Content-Length')):
            log.debug("%s: already cached" % download_url)
            return True
    parsed = urlparse.urlparse(swift_url)
    if local_path(download_url):
        class LocalDownload:
            def __init__(self, path):
                self.content = open(path, 'rb')
                self.ok = True
        resp = LocalDownload(download_url)
    else:
        resp = requests.get(download_url, stream=True)
    if resp.ok:
        log.debug("%s: caching to %s" % (download_url, swift_url))
        sig, expires = get_tempurl(parsed.path, swift_key)
        tempurl = "%s?temp_url_sig=%s&temp_url_expires=%s" % (
            swift_url, sig, expires)
        headers = None
        if swift_ttl:
            headers = {'X-Delete-After': swift_ttl}
        r = requests.put(tempurl, data=resp.content, headers=headers)
        return r.ok
    else:
        log.error("%s: get failed (%s)" % (download_url, str(resp)))
        return False


def get_config(filename):
    with open(filename, 'r') as fh:
        try:
            return(yaml.load(fh))
        except yaml.YAMLError as exc:
            raise exc


def add_enabled_repos(filename, section=None):
    y = yum.YumBase()
    rs = y.repos
    config = get_config(filename)
    data = config.get(section)
    if not data:
        return
    existing_ids = []
    for k in config[section]['mirrors']:
        existing_ids.append(k.get('name'))
    for r in rs.listEnabled():
        if r.id in existing_ids:
            continue
        config[section]['mirrors'].append(
            {'url': random.choice(r.urls),
             'prefix': '%s/' % r.id,
             'type': 'repodata',
             'name': r.id})
    with open(filename, 'wb') as fh:
        fh.write(yaml.dump(config, default_flow_style=False))


def execute(argv, cwd=None):
    p = subprocess.Popen(argv, cwd=cwd)
    if p.wait():
        raise RuntimeError("%s: failed (cwd=%s)" % (' '.join(argv), cwd))


def setup_log(args):
    lvl = log.DEBUG if args.debug else log.INFO
    log.basicConfig(format='*** %(levelname)s:\t%(message)s\033[m', level=lvl)
    log.getLogger("requests").setLevel(log.WARNING)


def main():
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument("--debug", action="store_const", const=True)
    parser.add_argument('filename', help="YAML config file")
    parser.add_argument(
        '--noop', action='store_true', help="Noop - only compare mirrors")
    parser.add_argument(
        '--update', action='store_true', help="Update objects if they exist \
        but differ in size. Objects are skipped by default if they exist")

    if 'yum' in sys.modules:
        parser.add_argument(
            '--add-enabled-repos', help="Add all currently enabled \
            repositories to the named section in the config file if not yet \
            existing. Randomly selects one mirror url",
            metavar='<section name>')

    args = parser.parse_args()
    setup_log(args)
    config = get_config(args.filename)
    if args.add_enabled_repos:
        add_enabled_repos(args.filename, args.add_enabled_repos)
        sys.exit(0)

    for name, entry in config.items():
        swift_url = entry.get('swift').get('url')
        swift_key = entry.get('swift').get('key')
        swift_ttl = entry.get('swift').get('ttl')

        for mirror in entry.get('mirrors'):
            mirror_name = mirror.get('name')
            prefix = mirror.get('prefix', '')
            mirror_url = mirror.get('url')
            mirror_type = mirror.get('type')

            if mirror_url[-1] != '/':
                mirror_url = "%s/" % mirror_url

            if mirror_type == 'repodata':
                log.info("Getting repodata_uri_list %s" % mirror_url)
                uris = get_repodata_uri_list(mirror_url)
            elif mirror_type == 'direct':
                uris = [mirror_url.split('/')[-2]]
                mirror_url = "/".join(mirror_url.split('/')[:-2]) + "/"
                log.info("Direct get %s from %s" % (uris[0], mirror_url))
            elif mirror_type == 'local':
                log.info("Getting local_files_list %s" % mirror_url)
                uris = get_local_files_list(mirror_url)
            elif mirror_type == 'git':
                cachedir = "%s/.cache/mirror2swift" % os.environ["HOME"]
                if not os.path.isdir(cachedir):
                    os.makedirs(cachedir)
                gitdir = "%s/%s" % (cachedir, mirror_name)
                if not os.path.isdir(gitdir):
                    log.info("Cloning %s to %s" % (mirror_url, gitdir))
                    execute(["git", "clone", "--bare", mirror_url, gitdir])
                else:
                    log.info("Updating %s" % mirror_url)
                    execute(["git", "fetch", "origin",
                             "+refs/heads/*:refs/heads/*",
                             "+refs/tags/*:refs/tags/*"], cwd=gitdir)
                execute(["git", "update-server-info"], cwd=gitdir)
                mirror_url = "%s/" % gitdir
                uris = get_local_files_list(mirror_url)
            else:
                log.info("Getting weblisting_uri_list %s" % mirror_url)
                uris = get_weblisting_uri_list(mirror_url)

            objs = []
            for obj in get_container_list(swift_url, prefix):
                objs.append(re.sub('^%s' % prefix, '', obj))

            if args.update:
                missing = uris
            else:
                missing = get_missing(uris, objs)
                for uri in uris:
                    if force_update(uri) and uri not in missing:
                        missing.add(uri)
            if not missing:
                log.info("%s [%s]: is up-to-date (%s%s)" % (
                         name, mirror_name, swift_url, prefix))
                continue
            # Make sure repomd.xml file is uploaded at the end
            missing = list(missing)
            for index_file in ("repodata/repomd.xml",):
                if index_file in missing:
                    missing.remove(index_file)
                    missing.append(index_file)
            log.info("Uploading %d missing files for mirror %s [%s] (%s%s)" % (
                     len(missing), name, mirror_name, swift_url, prefix))
            for m in missing:
                print m + "...",
                if args.noop:
                    continue
                download_url = "%s%s" % (mirror_url, m)
                swift_path = "%s%s%s" % (swift_url, prefix, m)
                if upload_missing(
                        download_url,
                        swift_path, swift_key, swift_ttl, args.update):
                    print "OK"
                else:
                    print "Failed"


if __name__ == "__main__":
    main()
