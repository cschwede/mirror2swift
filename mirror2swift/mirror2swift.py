#!/usr/bin/env python
import argparse
import hashlib
import hmac
import lxml.html
import re
import requests
import time
import urllib
import urlparse
import yaml
import gzip
import StringIO


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

    # Get repomod.xml
    resp = requests.get(repomd_url)
    dom = lxml.html.fromstring(resp.content)
    filelist = None
    for uri in dom.xpath('//location/@href'):
        uri_list.append(uri)

    # Get primary.xml.gz
    filelist = filter(lambda x: x.endswith("primary.xml.gz"), uri_list)
    if len(filelist) != 1:
        raise RuntimeError("Couldn't find filelist in %s (%s)" % (
                            repomd_url, uri_list))
    resp = requests.get("%s%s" % (base_url, filelist[0]))
    filelist = gzip.GzipFile(fileobj=StringIO.StringIO(resp.content)).read()

    # Extract packages list
    dom = lxml.html.fromstring(filelist)
    for uri in dom.xpath('//location/@href'):
        uri_list.append(uri)
    return uri_list


def get_container_list(url, prefix=None):
    url += "?format=json"
    if prefix:
        url += "&prefix=%s" % prefix
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


def upload_missing(download_url, swift_url, swift_key, update=False):
    if update:
        mirror_resp = requests.head(download_url)
        swift_resp = requests.head(swift_url)
        if (mirror_resp.headers.get('Content-Length') ==
                swift_resp.headers.get('Content-Length')):
            return True
    parsed = urlparse.urlparse(swift_url)
    resp = requests.get(download_url, stream=True)
    if resp.ok:
        sig, expires = get_tempurl(parsed.path, swift_key)
        tempurl = "%s?temp_url_sig=%s&temp_url_expires=%s" % (
            swift_url, sig, expires)
        r = requests.put(tempurl, data=resp.content)
        return r.ok
    else:
        return False


def get_config(filename):
    with open(filename, 'r') as fh:
        try:
            return(yaml.load(fh))
        except yaml.YAMLError as exc:
            raise exc


def main():
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('filename', help="YAML config file")
    parser.add_argument(
        '--noop', action='store_true', help="Noop - only compare mirrors")
    parser.add_argument(
        '--update', action='store_true', help="Update objects if they exist \
        but differ in size. Objects are skipped by default if they exist")

    args = parser.parse_args()
    config = get_config(args.filename)
    for name, entry in config.items():
        uris = []
        swift_url = entry.get('swift').get('url')
        swift_key = entry.get('swift').get('key')

        for mirror in entry.get('mirrors'):
            prefix = mirror.get('prefix', '')
            mirror_url = mirror.get('url')
            mirror_type = mirror.get('type')

            if mirror_url[-1] != '/':
                mirror_url = "%s/" % mirror_url

            if mirror_type == 'repodata':
                uris += get_repodata_uri_list(mirror_url)
            else:
                uris += get_weblisting_uri_list(mirror_url)

            objs = []
            for obj in get_container_list(swift_url, prefix):
                objs.append(re.sub('^%s' % prefix, '', obj))

            if args.update:
                missing = uris
            else:
                missing = get_missing(uris, objs)
            if not missing:
                continue
            print "Uploading %d missing files for mirror %s\n" % (
                len(missing), name)
            for m in missing:
                print m + "...",
                if args.noop:
                    continue
                download_url = "%s%s" % (mirror_url, m)
                swift_path = "%s%s%s" % (swift_url, prefix, m)
                if upload_missing(
                        download_url, swift_path, swift_key, args.update):
                    print "OK"
                else:
                    print "Failed"


if __name__ == "__main__":
    main()
