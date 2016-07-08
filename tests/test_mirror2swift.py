import tempfile
import unittest

from mock import call, patch, MagicMock

from mirror2swift import mirror2swift


class TestMirror2Swift(unittest.TestCase):
    exp_sig = 'f33c4ec86cea095f10ec721d5ec66f1bdb008950'

    @patch('requests.get')
    def test_get_uri_list(self, mock_get):
        html = """<a href="?C=N;O=D">Name</a>
                  <a href="../">../</a>
                  <a href="pkgs/">pkgs/</a>"""
        content = [
            MagicMock(content=html),
            MagicMock(content='<a href="sample%2B.rpm">sample+.rpm</a>')]
        mock_get.side_effect = content
        uris = mirror2swift.get_weblisting_uri_list('http://some/url/')
        mock_get.assert_has_calls(
            [call('http://some/url/'), call('http://some/url/pkgs/')])
        self.assertEqual(['pkgs/sample+.rpm'], uris)

    @patch('requests.get')
    def test_get_container_list(self, mock_get):
        mock_get.return_value.json = MagicMock(
            return_value=[{'name': 'Packages/sample+.el7.i686.rpm'}])
        uris = mirror2swift.get_container_list('http://some/url/')
        mock_get.assert_has_calls([call('http://some/url/?format=json')])
        self.assertEqual(['Packages/sample+.el7.i686.rpm'], uris)

        uris = mirror2swift.get_container_list('http://some/url/', 'prefix')
        mock_get.assert_has_calls(
            [call('http://some/url/?format=json&prefix=prefix')])

    def test_get_missing(self):
        uri_list = ['a', 'b', 'c', 'd']
        container_list = ['a', 'b', 'd']
        missing = mirror2swift.get_missing(uri_list, container_list)
        self.assertEqual(['c'], list(missing))

    def test_get_unneeded(self):
        uri_list = ['a', 'b', 'c', 'd']
        container_list = ['a', 'b', 'd', 'f']
        unneeded = mirror2swift.get_unneeded(uri_list, container_list)
        self.assertEqual(['f'], list(unneeded))

    @patch('time.time')
    def test_tempurl(self, mock_time):
        mock_time.return_value = 1000
        sig, expires = mirror2swift.get_tempurl('/v1/a/c/o', 'secret')
        self.assertEqual(self.exp_sig, sig)

    @patch('time.time')
    @patch('requests.put')
    @patch('requests.get')
    def test_upload_missing(self, mock_get, mock_put, mock_time):
        mock_time.return_value = 1000
        mock_get.return_value = MagicMock(content="body")
        mirror2swift.upload_missing(
            'http://downloadurl/', 'http://swifturl/v1/a/c/o', 'secret')
        mock_get.assert_has_calls([call('http://downloadurl/', stream=True)])
        mock_put.assert_has_calls([call(
            'http://swifturl/v1/a/c/o?temp_url_sig=%s&temp_url_expires=1300' %
            self.exp_sig, data='body')])

    def test_get_config(self):
        sample_config = """first:
  mirrors:
  - name: n
    url: 'http://mirror/'
    prefix: 'p/'
  swift:
    url: 'http://swift/'
    key: 'secret'
"""

        expected_config = {
            'first':
                {'swift':
                    {'url':
                        'http://swift/', 'key': 'secret'},
                 'mirrors': [{
                     'url': 'http://mirror/', 'prefix': 'p/', 'name': 'n'}]}}
        tmpfile = tempfile.NamedTemporaryFile(delete=False)
        tmpfile.write(sample_config)
        tmpfile.close()
        config = mirror2swift.get_config(tmpfile.name)
        self.assertEqual(expected_config, config)

    @patch('yum.YumBase.repos')
    def test_add_enabled_repos(self, mock_yum):
        class DummyRepo(object):
            def __init__(self):
                self.id = "something"
                self.urls = ["url"]
        sample_config = """first:
  mirrors:
  - name: n
    url: 'http://mirror/'
    prefix: 'p/'
  swift:
    url: 'http://swift/'
    key: 'secret'
"""
        _, fname = tempfile.mkstemp()
        with open(fname, "wb") as fh:
            fh.write(sample_config)
        mock_yum.listEnabled.return_value = [DummyRepo()]

        mirror2swift.add_enabled_repos(fname, section="first")
        with open(fname) as fh:
            returned = fh.read()
        expected = """first:
  mirrors:
  - name: n
    prefix: p/
    url: http://mirror/
  - name: something
    prefix: something/
    type: repodata
    url: url
  swift:
    key: secret
    url: http://swift/
"""
        self.assertEqual(expected, returned)
