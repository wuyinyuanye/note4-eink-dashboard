"""LAN routing must accept this host's address while retaining origin checks."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from app import App, DEFAULTS, Server, handler, control_url


class LanTests(unittest.TestCase):
    def test_lan_host_same_origin_and_foreign_host_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            app=App(DEFAULTS,Path(directory))
            server=Server(('0.0.0.0',0),handler(app,0))
            port=server.server_address[1];server.RequestHandlerClass=handler(app,port)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            address=control_url(port).split('//')[1]
            def request(method,path,headers=None,body=None):
                connection=http.client.HTTPConnection(address,timeout=3)
                try:
                    connection.request(method,path,body=body,headers=headers or {})
                    response=connection.getresponse();response.read();return response.status
                finally: connection.close()
            try:
                self.assertEqual(request('GET','/'),200)
                self.assertEqual(request('GET','/control.js'),200)
                self.assertEqual(request('GET','/api/state'),200)
                headers={'Content-Type':'application/json','Origin':'http://'+address}
                body=json.dumps({'codes':'sh000001','interval':300})
                self.assertEqual(request('POST','/api/market/settings',headers,body),200)
                self.assertEqual(request('GET','/',{'Host':'evil.example:'+str(port)}),403)
                headers['Origin']='http://evil.example'
                self.assertEqual(request('POST','/api/market/settings',headers,body),403)
                headers['Origin']='http://'+address;headers['Sec-Fetch-Site']='cross-site'
                self.assertEqual(request('POST','/api/market/settings',headers,body),403)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__': unittest.main()
