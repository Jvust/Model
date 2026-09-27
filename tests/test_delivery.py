import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from runtime import application

class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),application.ApplicationHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,method='GET',body=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=3)
        connection.request(method,path,body=body);response=connection.getresponse();content=response.read();status=response.status;connection.close();return status,content
    def test_bundled_home_page(self):
        status,body=self.request('/');self.assertEqual(status,200);self.assertIn(b'native-workspaces.js',body)
    def test_static_path_escape(self):self.assertEqual(self.request('/assets/%2e%2e/runtime/application.py')[0],404)
    def test_static_null_path(self):self.assertEqual(self.request('/assets/%00')[0],404)
    def test_chat_profile_requires_token(self):
        with patch.object(application.bridge,'REMOTE_TOKEN','testing'):
            self.assertEqual(self.request('/v1/chat/profile','POST','{}')[0],401)

if __name__=='__main__':unittest.main()
